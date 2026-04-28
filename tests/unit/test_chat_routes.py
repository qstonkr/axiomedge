"""Unit tests for /api/v1/chat routes — auth + state mocked."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import app as fastapi_app
from src.auth.dependencies import OrgContext, get_current_org, get_current_user
from src.auth.providers import AuthUser


USER_ID = "11111111-1111-1111-1111-111111111111"
ORG_ID = "default-org"
CONV_ID = "22222222-2222-2222-2222-222222222222"
MSG_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def fake_user():
    return AuthUser(
        sub=USER_ID,
        email="admin@knowledge.local",
        display_name="Admin",
        provider="internal",
        roles=["admin"],
        active_org_id=ORG_ID,
    )


@pytest.fixture
def fake_org():
    return OrgContext(id=ORG_ID, user_role_in_org="OWNER")


@pytest.fixture
def fake_repo():
    repo = AsyncMock()
    repo.create_conversation.return_value = uuid.UUID(CONV_ID)
    repo.list_conversations.return_value = []
    repo.rename_conversation.return_value = True
    repo.soft_delete_conversation.return_value = True
    repo.get_conversation.return_value = MagicMock(
        id=uuid.UUID(CONV_ID), kb_ids=["g-espa"], title="",
    )
    repo.list_messages.return_value = []
    repo.append_message.return_value = uuid.UUID(MSG_ID)
    return repo


@pytest.fixture
def client(fake_user, fake_org, fake_repo, monkeypatch):
    # Bypass AuthMiddleware (which reads AUTH_ENABLED at module-import time).
    monkeypatch.setattr("src.auth.middleware.AUTH_ENABLED", False)
    monkeypatch.setattr("src.auth.middleware._ANONYMOUS_USER", fake_user)
    fastapi_app.dependency_overrides[get_current_user] = lambda: fake_user
    fastapi_app.dependency_overrides[get_current_org] = lambda: fake_org

    # Inject chat_repo into the (real) AppState — _create_repositories wired
    # at startup needs DB; we bypass it for unit tests.
    from src.api.app import _state as real_state
    monkeypatch.setattr(real_state, "chat_repo", fake_repo)
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def test_create_conversation_returns_id(client, fake_repo):
    res = client.post("/api/v1/chat/conversations", json={"kb_ids": ["g-espa"]})
    assert res.status_code == 201
    assert res.json()["id"] == CONV_ID
    fake_repo.create_conversation.assert_awaited_once()


def test_list_conversations_empty(client):
    res = client.get("/api/v1/chat/conversations")
    assert res.status_code == 200
    assert res.json()["conversations"] == []


def test_rename_conversation_404_when_not_owner(client, fake_repo):
    fake_repo.rename_conversation.return_value = False
    res = client.patch(f"/api/v1/chat/conversations/{CONV_ID}", json={"title": "x"})
    assert res.status_code == 404


def test_delete_conversation(client, fake_repo):
    res = client.delete(f"/api/v1/chat/conversations/{CONV_ID}")
    assert res.status_code == 200
    fake_repo.soft_delete_conversation.assert_awaited_once()


def test_send_message_routes_through_search(client, fake_repo, monkeypatch):
    """Simple query → 'search' mode via route_query, hub_search invoked."""
    # Patch the underlying search/agentic at chat.py module level.
    fake_search_resp = MagicMock()
    fake_search_resp.answer = "8s 답변"
    fake_search_resp.chunks = [{"chunk_id": "c1"}]
    fake_search_resp.confidence = 0.8
    fake_search_resp.confidence_level = "high"
    fake_search_resp.query_type = "lookup"
    fake_search_resp.search_time_ms = 120.0
    fake_search_resp.crag_action = None

    fake_search = AsyncMock(return_value=fake_search_resp)
    monkeypatch.setattr("src.api.routes.chat.hub_search", fake_search)

    # Classifier returns FACTUAL → route_query takes the search branch.
    # Real QueryClassifier.classify is sync and returns ClassificationResult;
    # MagicMock matches that surface.
    from src.search.query_classifier import QueryType
    fake_classifier = MagicMock()
    fake_classifier.classify.return_value = MagicMock(
        query_type=QueryType.FACTUAL, confidence=0.9, matched_patterns=[],
    )
    from src.api.app import _state as real_state
    monkeypatch.setattr(real_state, "query_classifier", fake_classifier)

    res = client.post(
        f"/api/v1/chat/conversations/{CONV_ID}/messages",
        json={"content": "신촌점 차주 점검?"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode_used"] == "search"
    assert body["content"] == "8s 답변"
    fake_search.assert_awaited_once()
    # Two append_message calls: user turn + assistant turn.
    assert fake_repo.append_message.await_count == 2


def test_send_message_normalizes_chunks_and_bootstraps_title(client, fake_repo, monkeypatch):
    """Two regressions from PR8: chunks need normalization (frontend wants
    doc_title/snippet/owner) and the conversation title needs a synchronous
    fallback so the sidebar isn't stuck at "(제목 없음)" while waiting on the
    auto_title worker job (which can block for tens of seconds on a slow LLM).
    """
    from src.search.query_classifier import QueryType

    fake_search_resp = MagicMock()
    fake_search_resp.answer = "answer body"
    # Hub-search-shaped chunks — frontend needs normalized form.
    fake_search_resp.chunks = [
        {
            "id": "abc-1",
            "kb_id": "drp",
            "document_name": "정책 v3.2.pdf",
            "text": "본문 발췌",
            "rerank_score": 0.91,
        },
    ]
    fake_search_resp.confidence = 0.8
    fake_search_resp.confidence_level = "high"
    fake_search_resp.query_type = "factual"
    fake_search_resp.search_time_ms = 100.0
    fake_search_resp.crag_action = None
    fake_search_resp.corrected_query = None
    fake_search_resp.display_query = None
    fake_search_resp.expanded_terms = []

    monkeypatch.setattr(
        "src.api.routes.chat.hub_search",
        AsyncMock(return_value=fake_search_resp),
    )
    monkeypatch.setattr(
        "src.api.routes.chat.enqueue_job",
        AsyncMock(return_value=None),
        raising=False,
    )
    fake_classifier = MagicMock()
    fake_classifier.classify.return_value = MagicMock(
        query_type=QueryType.FACTUAL, confidence=0.9, matched_patterns=[],
    )
    from src.api.app import _state as real_state
    monkeypatch.setattr(real_state, "query_classifier", fake_classifier)

    res = client.post(
        f"/api/v1/chat/conversations/{CONV_ID}/messages",
        json={"content": "GS25 세마역점 가맹분쟁 알려줘"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    chunk0 = body["chunks"][0]
    assert chunk0["chunk_id"] == "abc-1"
    assert chunk0["doc_title"] == "정책 v3.2.pdf"
    assert chunk0["snippet"] == "본문 발췌"
    assert chunk0["score"] == 0.91
    assert chunk0["marker"] == 1

    # set_title_if_empty called with the first 30 chars of the user query —
    # synchronous fallback so the sidebar is meaningful immediately.
    fake_repo.set_title_if_empty.assert_awaited()
    args = fake_repo.set_title_if_empty.await_args.args
    assert args[1] == "GS25 세마역점 가맹분쟁 알려줘"  # ≤30 chars


def test_send_message_force_deep_routes_agentic(client, fake_repo, monkeypatch):
    fake_agentic_resp = MagicMock()
    fake_agentic_resp.answer = "deep answer"
    fake_agentic_resp.trace_id = "trace-123"
    fake_agentic_resp.confidence = 0.7
    fake_agentic_resp.iteration_count = 2
    fake_agentic_resp.estimated_cost_usd = 0.001
    fake_agentic_resp.llm_provider = "ollama"
    fake_agentic_resp.failure_reason = None
    fake_agentic_resp.errors = []

    fake_agentic = AsyncMock(return_value=fake_agentic_resp)
    monkeypatch.setattr("src.api.routes.chat.agentic_ask", fake_agentic)

    res = client.post(
        f"/api/v1/chat/conversations/{CONV_ID}/messages",
        json={"content": "복합 질문", "force_mode": "deep"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode_used"] == "agentic"
    assert body["trace_id"] == "trace-123"
    fake_agentic.assert_awaited_once()
