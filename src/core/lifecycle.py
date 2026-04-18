"""Lifecycle State Machine

Document lifecycle state machine with validation.
States: draft, published, under_review, archived, deleted
Auto-archive scheduling based on freshness.

Created: 2026-03-25
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LifecycleStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    UNDER_REVIEW = "under_review"
    ARCHIVED = "archived"
    DELETED = "deleted"


class TransitionError(Exception):
    pass


# Valid transitions
ALLOWED_TRANSITIONS: dict[LifecycleStatus, list[LifecycleStatus]] = {
    LifecycleStatus.DRAFT: [
        LifecycleStatus.PUBLISHED,
        LifecycleStatus.DELETED,
    ],
    LifecycleStatus.PUBLISHED: [
        LifecycleStatus.ARCHIVED,
        LifecycleStatus.UNDER_REVIEW,
        LifecycleStatus.DELETED,
    ],
    LifecycleStatus.UNDER_REVIEW: [
        LifecycleStatus.PUBLISHED,
        LifecycleStatus.ARCHIVED,
        LifecycleStatus.DELETED,
    ],
    LifecycleStatus.ARCHIVED: [
        LifecycleStatus.PUBLISHED,
        LifecycleStatus.DELETED,
    ],
    LifecycleStatus.DELETED: [],
}

# Auto-archive after 90 days, deletion grace period 30 days
AUTO_ARCHIVE_DAYS = 90
DELETION_GRACE_DAYS = 30

# Stale detection threshold
DEFAULT_STALE_DAYS = 180


class LifecycleStateMachine:
    """Document lifecycle state machine with auto-archive scheduling.

    SRP: State transitions and validation only.
    Persistence delegated to DocumentLifecycleRepository.
    """

    def __init__(
        self,
        lifecycle_repo: Any,
        stale_threshold_days: int = DEFAULT_STALE_DAYS,
    ) -> None:
        self._lifecycle_repo = lifecycle_repo
        self._stale_threshold_days = stale_threshold_days

    async def get_or_create(
        self,
        document_id: str,
        kb_id: str,
    ) -> dict[str, Any]:
        """Get existing lifecycle or create draft."""
        existing = await self._lifecycle_repo.get_by_document(document_id, kb_id)
        if existing:
            return existing

        now = _utc_now()
        lifecycle_id = str(uuid.uuid4())
        data: dict[str, Any] = {
            "id": lifecycle_id,
            "document_id": document_id,
            "kb_id": kb_id,
            "status": LifecycleStatus.DRAFT.value,
            "previous_status": None,
            "status_changed_at": now,
            "status_changed_by": None,
            "auto_archive_at": None,
            "deletion_scheduled_at": None,
            "created_at": now,
            "updated_at": now,
            "transitions": [],
        }
        await self._lifecycle_repo.save(data)
        logger.info(
            "Created lifecycle: doc_id=%s kb_id=%s status=draft",
            document_id,
            kb_id,
        )
        return data

    async def transition(
        self,
        document_id: str,
        kb_id: str,
        from_state: str,
        to_state: str,
        actor: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Transition a document to a new state.

        Validates transition, records history, schedules auto-archive/deletion.

        Raises:
            TransitionError: If transition is not allowed.
        """
        lifecycle = await self.get_or_create(document_id, kb_id)

        current = lifecycle.get("status", "draft")
        if current != from_state:
            raise TransitionError(
                f"Current state is '{current}', expected '{from_state}'"
            )

        # Validate transition
        try:
            current_status = LifecycleStatus(from_state)
            target_status = LifecycleStatus(to_state)
        except ValueError as exc:
            raise TransitionError(f"Invalid state: {exc}") from exc

        allowed = ALLOWED_TRANSITIONS.get(current_status, [])
        if target_status not in allowed:
            raise TransitionError(
                f"Cannot transition from {from_state} to {to_state}"
            )

        now = _utc_now()

        # Build transition record
        transition_record = {
            "from_status": from_state,
            "to_status": to_state,
            "transitioned_by": actor,
            "transitioned_at": now,
            "reason": reason,
        }

        # Update lifecycle data
        lifecycle["previous_status"] = current
        lifecycle["status"] = to_state
        lifecycle["status_changed_at"] = now
        lifecycle["status_changed_by"] = actor
        lifecycle["updated_at"] = now

        # Ensure transitions list
        transitions = lifecycle.get("transitions", [])
        transitions.append(transition_record)
        lifecycle["transitions"] = transitions

        # Special handling
        if target_status == LifecycleStatus.DELETED:
            lifecycle["deletion_scheduled_at"] = now + timedelta(days=DELETION_GRACE_DAYS)
        elif target_status == LifecycleStatus.PUBLISHED:
            lifecycle["auto_archive_at"] = now + timedelta(days=AUTO_ARCHIVE_DAYS)
            lifecycle["deletion_scheduled_at"] = None

        await self._lifecycle_repo.save(lifecycle)

        logger.info(
            "Lifecycle transition: doc=%s %s->%s by=%s",
            document_id,
            from_state,
            to_state,
            actor,
        )
        return lifecycle

    async def publish(
        self,
        document_id: str,
        kb_id: str,
        actor: str,
    ) -> dict[str, Any]:
        """Convenience: transition to published."""
        lifecycle = await self.get_or_create(document_id, kb_id)
        current = lifecycle.get("status", "draft")
        return await self.transition(
            document_id, kb_id, current, "published", actor, reason="Published"
        )

    async def archive(
        self,
        document_id: str,
        kb_id: str,
        actor: str,
        reason: str = "Manual archive",
    ) -> dict[str, Any]:
        lifecycle = await self.get_or_create(document_id, kb_id)
        current = lifecycle.get("status", "published")
        return await self.transition(
            document_id, kb_id, current, "archived", actor, reason=reason
        )

    async def get_auto_archive_candidates(
        self, kb_id: str,
    ) -> list[dict[str, Any]]:
        """Find published documents past their auto-archive date."""
        docs = await self._lifecycle_repo.list_by_status(kb_id, "published")
        now = _utc_now()
        candidates = []
        for doc in docs:
            auto_at = doc.get("auto_archive_at")
            if auto_at and isinstance(auto_at, datetime) and now >= auto_at:
                candidates.append(doc)
        return candidates

    async def get_stale_documents(
        self,
        kb_id: str,
        last_modified_map: dict[str, datetime] | None = None,
    ) -> list[dict[str, Any]]:
        """Find published documents that are stale based on modification time."""
        if not last_modified_map:
            return []

        docs = await self._lifecycle_repo.list_by_status(kb_id, "published")
        stale = []
        now = _utc_now()
        for doc in docs:
            doc_id = doc.get("document_id", "")
            last_mod = last_modified_map.get(doc_id)
            if last_mod and (now - last_mod).days >= self._stale_threshold_days:
                doc["_days_since_modified"] = (now - last_mod).days
                stale.append(doc)
        return stale

    async def list_by_status(
        self,
        kb_id: str,
        status: str,
    ) -> list[dict[str, Any]]:
        return await self._lifecycle_repo.list_by_status(kb_id, status)

    async def list_all(self, kb_id: str) -> list[dict[str, Any]]:
        return await self._lifecycle_repo.list_by_kb(kb_id)
