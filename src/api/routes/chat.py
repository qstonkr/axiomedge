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
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.api.routes.agentic import AgenticAskRequest, agentic_ask
from src.api.routes.chat_helpers import derive_signals, route_query
from src.api.routes.search import HubSearchRequest, hub_search
from src.api.state import AppState
from src.auth.dependencies import OrgContext, get_current_org, get_current_user
from src.auth.providers import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])

# Inner /search/hub and /agentic/ask cap content at 2000 chars; mirror that
# here so a 2001+ char message fails validation BEFORE we persist a user turn,
# avoiding orphan rows on subsequent 502.
MAX_CONTENT_LENGTH = 2000

# Snippet preview shown in SourcePanel cards. Trimmed at write time to keep
# JSON payloads bounded (raw chunks can be multi-KB).
SNIPPET_MAX_CHARS = 500


def _normalize_chunk(raw: dict[str, Any], idx: int) -> dict[str, Any]:
    """Map hub_search / agentic chunk shape to the SourceChunk contract the
    frontend SourcePanel renders.

    Hub search emits ``{id, kb_id, document_id, document_name, title, text,
    content, rerank_score, score, ...}``. The frontend expects
    ``{chunk_id, marker, doc_title, kb_id, snippet, score, owner}``. Without
    this mapping the cards show only the kb_id (whatever happens to overlap),
    which is what users actually saw after PR8 landed.
    """
    chunk_id = raw.get("chunk_id") or raw.get("id") or f"c{idx}"
    doc_title = (
        raw.get("doc_title")
        or raw.get("document_name")
        or raw.get("title")
        or raw.get("document_id")
        or "(제목 없음)"
    )
    snippet_src = raw.get("snippet") or raw.get("text") or raw.get("content") or ""
    snippet = snippet_src[:SNIPPET_MAX_CHARS] if isinstance(snippet_src, str) else ""
    score = (
        raw.get("score")
        if isinstance(raw.get("score"), (int, float))
        else raw.get("rerank_score")
    )
    owner = raw.get("owner")
    if owner is None:
        meta = raw.get("metadata") or {}
        if isinstance(meta, dict):
            owner = meta.get("owner") or meta.get("owner_user_id")
    return {
        "chunk_id": str(chunk_id),
        "marker": idx + 1,
        "doc_title": str(doc_title),
        "kb_id": str(raw.get("kb_id") or ""),
        "snippet": snippet,
        "score": float(score) if isinstance(score, (int, float)) else None,
        "owner": owner,
    }


def _get_state() -> AppState:
    """Late-bound state accessor — patched in tests."""
    from src.api.app import _get_state as _gs
    return _gs()


def _get_repo():
    state = _get_state()
    repo = state.chat_repo
    if repo is None:
        raise HTTPException(503, detail="Chat service not initialized")
    return repo


def _set_audit(request: Request, event_type: str, user: AuthUser, **details: Any) -> None:
    """Record a chat event in the audit trail. AuditLogMiddleware reads
    request.state.audit on response and writes one row to audit_log."""
    request.state.audit = {
        "event_type": event_type,
        "actor": user.sub,
        "details": details,
    }


# --- request/response models ---------------------------------------------


class CreateConversationRequest(BaseModel):
    kb_ids: list[str] = Field(default_factory=list)


class CreateConversationResponse(BaseModel):
    id: str


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)
    # Pydantic-validated literal — narrowed before passing to route_query.
    force_mode: Literal["quick", "deep"] | None = None
    # Per-message KB scope override. When None, the conversation's stored kb_ids
    # are used. KbSelector chip toggles flow through here so users can widen or
    # narrow KB scope mid-conversation without having to start a new chat.
    kb_ids: list[str] | None = None


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
    request: Request,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
):
    repo = _get_repo()
    conv_id = await repo.create_conversation(
        user_id=uuid.UUID(user.sub),
        org_id=org.id,
        kb_ids=body.kb_ids,
    )
    _set_audit(
        request, "chat.conversation.create", user,
        conversation_id=str(conv_id), kb_ids=body.kb_ids,
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
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    repo = _get_repo()
    ok = await repo.rename_conversation(conv_id, uuid.UUID(user.sub), body.title)
    if not ok:
        raise HTTPException(404, detail="Conversation not found")
    _set_audit(
        request, "chat.conversation.rename", user,
        conversation_id=str(conv_id), title=body.title,
    )
    return {"status": "ok"}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(
    conv_id: uuid.UUID,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    repo = _get_repo()
    ok = await repo.soft_delete_conversation(conv_id, uuid.UUID(user.sub))
    if not ok:
        raise HTTPException(404, detail="Conversation not found")
    _set_audit(
        request, "chat.conversation.delete", user,
        conversation_id=str(conv_id),
    )
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
    # Pass user_id for defense-in-depth — list_messages enforces ownership too.
    msgs = await repo.list_messages(conv_id, user_id=uuid.UUID(user.sub))
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
    request: Request,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
):
    repo = _get_repo()
    conv = await repo.get_conversation(conv_id, uuid.UUID(user.sub))
    if conv is None:
        raise HTTPException(404, detail="Conversation not found")

    # KB scope: per-message override > conversation default. Empty list means
    # "no KB filter" (search all). None means "fall back to conversation default".
    effective_kb_ids = body.kb_ids if body.kb_ids is not None else list(conv.kb_ids)

    # Save user turn
    await repo.append_message(
        conversation_id=conv_id, role="user",
        content=body.content, chunks=[], meta={},
    )

    # Route via classifier signals. derive_signals is fail-open: classifier
    # missing or raising → conservative "search" defaults, never blocks send.
    state = _get_state()
    classifier = state.query_classifier
    sig = derive_signals(body.content, classifier)
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
                    kb_ids=effective_kb_ids or None,
                    top_k=8,
                    include_answer=True,
                ),
                user=user,
                org=org,
            )
            # res is HubSearchResponse pydantic model
            answer = res.answer or ""
            # Normalize chunk shape so SourcePanel cards have doc_title /
            # snippet / owner — not just the kb_id.
            chunks = [
                _normalize_chunk(c, i)
                for i, c in enumerate(res.chunks or [])
                if isinstance(c, dict)
            ]
            meta.update({
                "confidence": res.confidence,
                "confidence_level": res.confidence_level,
                "query_type": res.query_type,
                "search_time_ms": res.search_time_ms,
                "crag_action": res.crag_action,
                # Preserve corrected/expanded query for the meta tab
                "corrected_query": res.corrected_query,
                "display_query": res.display_query,
                "expanded_terms": list(res.expanded_terms or []),
            })
        else:
            res = await agentic_ask(
                request=AgenticAskRequest(
                    query=body.content,
                    kb_ids=effective_kb_ids or None,
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
        # Surface enough detail so a 502 doesn't carry an empty body when the
        # exception's __str__ is empty (CancelledError, loop-affinity errors,
        # asyncpg internal-protocol errors all behave that way).
        err_type = type(e).__name__
        err_msg = str(e) or err_type
        logger.exception(
            "Chat send: underlying %s call failed (%s): %s",
            mode, err_type, repr(e),
        )
        raise HTTPException(
            502, detail=f"underlying {mode} failed: {err_type}: {err_msg}",
        ) from e

    msg_id = await repo.append_message(
        conversation_id=conv_id, role="assistant",
        content=answer, chunks=chunks, meta=meta, trace_id=trace_id,
    )

    # Title bootstrap: when the conversation has no title yet, write a
    # synchronous fallback (first 30 chars of the user query) before the
    # response returns. This guarantees the sidebar shows something
    # meaningful immediately — invalidate-on-send picks it up before the
    # background LLM job has a chance to run. The worker job still runs
    # for the LLM-upgraded title; it uses set_title_if_empty so it skips
    # rather than overwrite our fallback. Trade-off accepted: cleaner
    # default UX over a slightly nicer (but possibly minutes-late) title.
    if not (conv.title or ""):  # type: ignore[truthy-bool]
        try:
            fallback = (body.content or "").strip()[:30] or "(제목 없음)"
            await repo.set_title_if_empty(conv_id, fallback)
            from src.jobs.queue import enqueue_job
            await enqueue_job(
                "auto_title_for_conversation",
                str(conv_id), body.content,
            )
        except Exception as e:  # noqa: BLE001 — title is best-effort
            logger.warning("auto_title bootstrap failed: %s", e)

    _set_audit(
        request, "chat.message.send", user,
        conversation_id=str(conv_id), mode_used=mode,
        kb_ids=effective_kb_ids,
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
