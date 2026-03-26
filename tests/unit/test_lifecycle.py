"""Unit tests for LifecycleStateMachine."""

from __future__ import annotations

from typing import Any

import pytest

from src.domain.lifecycle import (
    ALLOWED_TRANSITIONS,
    LifecycleStateMachine,
    LifecycleStatus,
    TransitionError,
)


class InMemoryLifecycleRepo:
    """Simple in-memory lifecycle repository for testing."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def get_by_document(self, document_id: str, kb_id: str) -> dict[str, Any] | None:
        key = f"{document_id}:{kb_id}"
        return self._store.get(key)

    async def save(self, data: dict[str, Any]) -> None:
        key = f"{data['document_id']}:{data['kb_id']}"
        self._store[key] = data

    async def list_by_status(self, kb_id: str, status: str) -> list[dict[str, Any]]:
        return [
            v for v in self._store.values()
            if v.get("kb_id") == kb_id and v.get("status") == status
        ]

    async def list_by_kb(self, kb_id: str) -> list[dict[str, Any]]:
        return [v for v in self._store.values() if v.get("kb_id") == kb_id]


@pytest.fixture
def sm() -> LifecycleStateMachine:
    return LifecycleStateMachine(lifecycle_repo=InMemoryLifecycleRepo())


class TestLifecycleStateMachine:

    @pytest.mark.asyncio
    async def test_valid_transition_draft_to_published(self, sm: LifecycleStateMachine):
        """draft -> published should succeed."""
        lifecycle = await sm.get_or_create("doc-1", "kb-1")
        assert lifecycle["status"] == "draft"

        result = await sm.transition("doc-1", "kb-1", "draft", "published", "user-a")
        assert result["status"] == "published"
        assert result["previous_status"] == "draft"
        assert result["status_changed_by"] == "user-a"
        assert result["auto_archive_at"] is not None  # published -> auto-archive scheduled

    @pytest.mark.asyncio
    async def test_invalid_transition_draft_to_deleted(self, sm: LifecycleStateMachine):
        """draft -> deleted should succeed (it IS allowed per ALLOWED_TRANSITIONS)."""
        await sm.get_or_create("doc-2", "kb-1")
        result = await sm.transition("doc-2", "kb-1", "draft", "deleted", "user-b")
        assert result["status"] == "deleted"
        assert result["deletion_scheduled_at"] is not None

    @pytest.mark.asyncio
    async def test_invalid_transition_draft_to_archived(self, sm: LifecycleStateMachine):
        """draft -> archived should fail (not in allowed transitions)."""
        await sm.get_or_create("doc-3", "kb-1")
        with pytest.raises(TransitionError, match="Cannot transition"):
            await sm.transition("doc-3", "kb-1", "draft", "archived", "user-c")

    @pytest.mark.asyncio
    async def test_transition_history_recorded(self, sm: LifecycleStateMachine):
        """Each transition should be recorded in the transitions list."""
        await sm.get_or_create("doc-4", "kb-1")

        await sm.transition("doc-4", "kb-1", "draft", "published", "user-a", reason="Ready")
        result = await sm.transition(
            "doc-4", "kb-1", "published", "under_review", "user-b", reason="Needs review"
        )

        transitions = result["transitions"]
        assert len(transitions) == 2
        assert transitions[0]["from_status"] == "draft"
        assert transitions[0]["to_status"] == "published"
        assert transitions[0]["reason"] == "Ready"
        assert transitions[1]["from_status"] == "published"
        assert transitions[1]["to_status"] == "under_review"

    def test_allowed_transitions(self):
        """Verify the transition map structure."""
        # DRAFT can go to PUBLISHED or DELETED
        assert LifecycleStatus.PUBLISHED in ALLOWED_TRANSITIONS[LifecycleStatus.DRAFT]
        assert LifecycleStatus.DELETED in ALLOWED_TRANSITIONS[LifecycleStatus.DRAFT]
        assert LifecycleStatus.ARCHIVED not in ALLOWED_TRANSITIONS[LifecycleStatus.DRAFT]

        # DELETED is terminal
        assert ALLOWED_TRANSITIONS[LifecycleStatus.DELETED] == []

        # ARCHIVED can be republished or deleted
        assert LifecycleStatus.PUBLISHED in ALLOWED_TRANSITIONS[LifecycleStatus.ARCHIVED]
        assert LifecycleStatus.DELETED in ALLOWED_TRANSITIONS[LifecycleStatus.ARCHIVED]
