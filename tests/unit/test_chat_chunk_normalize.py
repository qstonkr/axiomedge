"""_normalize_chunk maps hub_search/agentic chunks → SourceChunk shape.

Drift here was the cause of the post-PR8 bug where SourcePanel cards showed
only the kb_id ("drp") with no doc title or snippet — the frontend expected
``doc_title`` / ``snippet`` / ``owner`` fields the backend never emitted.
"""

from __future__ import annotations

from src.api.routes.chat import _normalize_chunk


def test_hub_search_shape_with_text_and_document_name():
    raw = {
        "id": "abc-123",
        "kb_id": "drp",
        "document_name": "정책 v3.2.pdf",
        "text": "본문 일부…",
        "rerank_score": 0.93,
    }
    out = _normalize_chunk(raw, idx=0)
    assert out["chunk_id"] == "abc-123"
    assert out["marker"] == 1
    assert out["kb_id"] == "drp"
    assert out["doc_title"] == "정책 v3.2.pdf"
    assert out["snippet"] == "본문 일부…"
    assert out["score"] == 0.93


def test_agentic_shape_with_content_and_title():
    raw = {
        "chunk_id": "id-1",
        "kb_id": "g-espa",
        "title": "회의록",
        "content": "x" * 1000,
        "score": 0.7,
    }
    out = _normalize_chunk(raw, idx=2)
    assert out["chunk_id"] == "id-1"
    assert out["marker"] == 3
    assert out["doc_title"] == "회의록"
    # Snippet is truncated to bound JSON payload size
    assert len(out["snippet"]) == 500


def test_owner_pulled_from_metadata_dict():
    raw = {
        "kb_id": "drp",
        "metadata": {"owner": "김철수"},
    }
    out = _normalize_chunk(raw, idx=0)
    assert out["owner"] == "김철수"


def test_missing_fields_yield_safe_defaults():
    out = _normalize_chunk({}, idx=0)
    assert out["chunk_id"] == "c0"
    assert out["doc_title"] == "(제목 없음)"
    assert out["snippet"] == ""
    assert out["score"] is None
    assert out["owner"] is None
    assert out["kb_id"] == ""
