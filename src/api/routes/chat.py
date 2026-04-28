"""Chat surface API — conversations + messages.

Conversations: persistent left sidebar history (PIPA: 90d retention,
pgcrypto-encrypted body, user-scoped soft delete).

Messages POST is a wrapper that auto-routes to existing /search/hub or
/agentic/ask via route_query (server-side mode router replaces the
prior user-facing ModeToggle).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.routes.agentic import AgenticAskRequest, agentic_ask
from src.api.routes.chat_helpers import derive_signals, route_query
from src.api.routes.search import HubSearchRequest, hub_search
from src.auth.dependencies import OrgContext, get_current_org, get_current_user
from src.auth.providers import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


def _get_state() -> dict:  # noqa: ANN201
    """Late-bound state accessor — patched in tests."""
    from src.api.app import _get_state as _gs
    return _gs()


def _get_repo():
    state = _get_state()
    repo = state.get("chat_repo") if hasattr(state, "get") else getattr(state, "chat_repo", None)
    if repo is None:
        raise HTTPException(503, detail="Chat service not initialized")
    return repo


# --- request/response models ---------------------------------------------


class CreateConversationRequest(BaseModel):
    kb_ids: list[str] = Field(default_factory=list)


class CreateConversationResponse(BaseModel):
    id: str


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    force_mode: str | None = None  # 'quick' | 'deep' | None


class ConversationView(BaseModel):
    id: str
    title: str
    kb_ids: list[str]
    updated_at: str


class MessageView(BaseModel):
    id: str
    role: str
    content: str
    chunks: list[dict[str, Any]]
    meta: dict[str, Any]
    trace_id: str | None
    created_at: str


# --- routes --------------------------------------------------------------


@router.post(
    "/conversations",
    response_model=CreateConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    body: CreateConversationRequest,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
):
    repo = _get_repo()
    conv_id = await repo.create_conversation(
        user_id=uuid.UUID(user.sub),
        org_id=org.id,
        kb_ids=body.kb_ids,
    )
    return {"id": str(conv_id)}


@router.get("/conversations")
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    user: AuthUser = Depends(get_current_user),
):
    repo = _get_repo()
    rows = await repo.list_conversations(uuid.UUID(user.sub), limit, offset)
    return {
        "conversations": [
            ConversationView(
                id=str(r.id),
                title=r.title,
                kb_ids=list(r.kb_ids),
                updated_at=r.updated_at.isoformat(),
            ).model_dump()
            for r in rows
        ],
    }


@router.patch("/conversations/{conv_id}")
async def rename_conversation(
    conv_id: uuid.UUID,
    body: RenameRequest,
    user: AuthUser = Depends(get_current_user),
):
    repo = _get_repo()
    ok = await repo.rename_conversation(conv_id, uuid.UUID(user.sub), body.title)
    if not ok:
        raise HTTPException(404, detail="Conversation not found")
    return {"status": "ok"}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(
    conv_id: uuid.UUID,
    user: AuthUser = Depends(get_current_user),
):
    repo = _get_repo()
    ok = await repo.soft_delete_conversation(conv_id, uuid.UUID(user.sub))
    if not ok:
        raise HTTPException(404, detail="Conversation not found")
    return {"status": "ok"}


@router.get("/conversations/{conv_id}/messages")
async def list_messages(
    conv_id: uuid.UUID,
    user: AuthUser = Depends(get_current_user),
):
    repo = _get_repo()
    conv = await repo.get_conversation(conv_id, uuid.UUID(user.sub))
    if conv is None:
        raise HTTPException(404, detail="Conversation not found")
    msgs = await repo.list_messages(conv_id)
    return {
        "messages": [
            MessageView(
                id=str(m.id),
                role=m.role,
                content=m.content,
                chunks=m.chunks,
                meta=m.meta,
                trace_id=m.trace_id,
                created_at=m.created_at.isoformat(),
            ).model_dump()
            for m in msgs
        ],
    }


@router.post("/conversations/{conv_id}/messages")
async def send_message(
    conv_id: uuid.UUID,
    body: SendMessageRequest,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
):
    repo = _get_repo()
    conv = await repo.get_conversation(conv_id, uuid.UUID(user.sub))
    if conv is None:
        raise HTTPException(404, detail="Conversation not found")

    # Save user turn
    await repo.append_message(
        conversation_id=conv_id, role="user",
        content=body.content, chunks=[], meta={},
    )

    # Route via classifier signals (None classifier → defaults to 'search').
    state = _get_state()
    classifier = state.get("query_classifier") if hasattr(state, "get") else getattr(state, "query_classifier", None)
    sig = await derive_signals(body.content, classifier)
    mode = route_query(sig, force_mode=body.force_mode)

    answer: str = ""
    chunks: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"mode_used": mode}
    trace_id: str | None = None

    try:
        if mode == "search":
            res = await hub_search(
                request=HubSearchRequest(
                    query=body.content,
                    kb_ids=list(conv.kb_ids) or None,
                    top_k=8,
                    include_answer=True,
                ),
                user=user,
                org=org,
            )
            # res is HubSearchResponse pydantic model
            answer = res.answer or ""
            chunks = list(res.chunks or [])
            meta.update({
                "confidence": res.confidence,
                "confidence_level": res.confidence_level,
                "query_type": res.query_type,
                "search_time_ms": res.search_time_ms,
                "crag_action": res.crag_action,
            })
        else:
            res = await agentic_ask(
                request=AgenticAskRequest(
                    query=body.content,
                    kb_ids=list(conv.kb_ids) or None,
                ),
                user=user,
                org=org,
            )
            answer = res.answer
            trace_id = res.trace_id
            meta.update({
                "confidence": res.confidence,
                "iteration_count": res.iteration_count,
                "estimated_cost_usd": res.estimated_cost_usd,
                "llm_provider": res.llm_provider,
                "failure_reason": res.failure_reason,
            })
    except HTTPException:
        # Re-raise 4xx/5xx from underlying routes after still having recorded user turn.
        raise
    except Exception as e:  # noqa: BLE001 — surface as 502 so the user turn is preserved
        logger.exception("Chat send: underlying %s call failed", mode)
        raise HTTPException(502, detail=f"underlying {mode} failed: {e}") from e

    msg_id = await repo.append_message(
        conversation_id=conv_id, role="assistant",
        content=answer, chunks=chunks, meta=meta, trace_id=trace_id,
    )

    return {
        "id": str(msg_id),
        "role": "assistant",
        "content": answer,
        "chunks": chunks,
        "meta": meta,
        "trace_id": trace_id,
        "mode_used": mode,
    }
