"""CRUD + query helpers for graph_schema_candidates (Phase 3).

Spec §6.3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.stores.postgres.models import SchemaCandidateModel
from src.stores.postgres.repositories.base import BaseRepository


class SchemaCandidateRepo(BaseRepository):
    def __init__(self, session_maker: async_sessionmaker) -> None:
        super().__init__(session_maker)

    async def upsert(
        self,
        *,
        kb_id: str,
        candidate_type: str,
        label: str,
        confidence: float,
        examples: list[dict[str, Any]],
        source_label: str | None = None,
        target_label: str | None = None,
        similar_labels: list[dict[str, Any]] | None = None,
    ) -> None:
        """Insert or increment-frequency update keyed on (kb, type, label)."""
        async with self._session_maker() as session:
            async with session.begin():
                existing = await session.scalar(
                    select(SchemaCandidateModel).where(
                        SchemaCandidateModel.kb_id == kb_id,
                        SchemaCandidateModel.candidate_type == candidate_type,
                        SchemaCandidateModel.label == label,
                    ),
                )
                if existing is None:
                    session.add(SchemaCandidateModel(
                        kb_id=kb_id,
                        candidate_type=candidate_type,
                        label=label,
                        frequency=1,
                        confidence_avg=confidence,
                        confidence_min=confidence,
                        confidence_max=confidence,
                        source_label=source_label,
                        target_label=target_label,
                        examples=examples,
                        similar_labels=similar_labels or [],
                        status="pending",
                    ))
                else:
                    new_freq = existing.frequency + 1
                    new_avg = (
                        (existing.confidence_avg * existing.frequency + confidence)
                        / new_freq
                    )
                    existing.frequency = new_freq
                    existing.confidence_avg = new_avg
                    existing.confidence_min = min(existing.confidence_min, confidence)
                    existing.confidence_max = max(existing.confidence_max, confidence)
                    existing.last_seen_at = datetime.now(UTC)
                    # Keep at most 5 latest examples (admin review UX)
                    existing.examples = (
                        list(examples) + list(existing.examples or [])
                    )[:5]

    async def list_pending(self, kb_id: str) -> list[SchemaCandidateModel]:
        async with self._session_maker() as session:
            result = await session.execute(
                select(SchemaCandidateModel).where(
                    SchemaCandidateModel.kb_id == kb_id,
                    SchemaCandidateModel.status == "pending",
                ).order_by(SchemaCandidateModel.frequency.desc()),
            )
            return list(result.scalars().all())

    async def list_approved_labels(
        self, kb_id: str, candidate_type: str,
    ) -> list[str]:
        async with self._session_maker() as session:
            result = await session.execute(
                select(SchemaCandidateModel.label).where(
                    SchemaCandidateModel.kb_id == kb_id,
                    SchemaCandidateModel.candidate_type == candidate_type,
                    SchemaCandidateModel.status == "approved",
                ),
            )
            return [row[0] for row in result.all()]

    async def decide(
        self,
        *,
        kb_id: str,
        candidate_type: str,
        label: str,
        status: str,
        decided_by: str,
        merged_into: str | None = None,
        rejected_reason: str | None = None,
    ) -> None:
        """Terminal transition: approved|rejected|merged."""
        async with self._session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(SchemaCandidateModel).where(
                        SchemaCandidateModel.kb_id == kb_id,
                        SchemaCandidateModel.candidate_type == candidate_type,
                        SchemaCandidateModel.label == label,
                    ).values(
                        status=status,
                        decided_at=datetime.now(UTC),
                        decided_by=decided_by,
                        merged_into=merged_into,
                        rejected_reason=rejected_reason,
                    ),
                )


__all__ = ["SchemaCandidateRepo"]
