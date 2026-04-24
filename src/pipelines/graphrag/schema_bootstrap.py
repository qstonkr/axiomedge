"""Schema bootstrap — LLM-based discovery of new entity/relation types.

Dependency-injected orchestrator: takes ``llm`` / ``candidate_repo`` /
``run_repo`` / ``sampler`` as Protocols so unit tests can mock each.
Real wiring lives in ``src/jobs/schema_bootstrap_jobs.py``.

Spec §6.3 + §6.5 (concurrency).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from src.pipelines.graphrag.schema_prompts import (
    SCHEMA_DISCOVERY_PROMPT,
    DiscoveryResponse,
    parse_discovery_response,
)

logger = logging.getLogger(__name__)


@dataclass
class BootstrapConfig:
    sample_size: int = 100
    min_per_source: int = 5
    confidence_threshold: float = 0.8
    similarity_threshold: float = 0.7
    batch_size: int = 10
    doc_preview_chars: int = 1500


class DocSampler(Protocol):
    async def sample(
        self, *, kb_id: str, sample_size: int,
    ) -> list[dict[str, Any]]:
        """Return [{doc_id, content, source_type}, ...]."""
        ...


class LLMClient(Protocol):
    def invoke(self, *, document: str, prompt_template: str) -> str: ...


class CandidateRepoProto(Protocol):
    async def upsert(
        self, *,
        kb_id: str, candidate_type: str, label: str,
        confidence: float, examples: list[dict[str, Any]],
        source_label: str | None = None,
        target_label: str | None = None,
        similar_labels: list[dict[str, Any]] | None = None,
    ) -> None: ...

    async def list_approved_labels(
        self, kb_id: str, candidate_type: str,
    ) -> list[str]: ...


class RunRepoProto(Protocol):
    async def has_running(self, kb_id: str) -> bool: ...
    async def create(
        self, *,
        kb_id: str, triggered_by: str,
        sample_size: int, sample_strategy: str,
        triggered_by_user: str | None = None,
    ) -> UUID: ...
    async def complete(
        self, run_id: UUID, *,
        status: str,
        docs_scanned: int = 0, candidates_found: int = 0,
        llm_calls: int = 0, error_message: str | None = None,
    ) -> None: ...


class BootstrapAlreadyRunning(RuntimeError):
    pass


class SchemaBootstrapper:
    def __init__(
        self,
        *,
        llm: LLMClient,
        candidate_repo: CandidateRepoProto,
        run_repo: RunRepoProto,
        sampler: DocSampler,
    ) -> None:
        self.llm = llm
        self.candidates = candidate_repo
        self.runs = run_repo
        self.sampler = sampler

    async def run(
        self,
        *,
        kb_id: str,
        triggered_by: str,
        triggered_by_user: str | None = None,
        config: BootstrapConfig | None = None,
    ) -> UUID:
        """One bootstrap iteration. Raises BootstrapAlreadyRunning on conflict.

        Any exception inside the main try-block marks the run as 'failed'
        before re-raising — monitoring can alert on repeat failures.
        """
        cfg = config or BootstrapConfig()

        if await self.runs.has_running(kb_id):
            raise BootstrapAlreadyRunning(
                f"Bootstrap already running for kb_id={kb_id}",
            )

        run_id = await self.runs.create(
            kb_id=kb_id, triggered_by=triggered_by,
            sample_size=cfg.sample_size, sample_strategy="stratified",
            triggered_by_user=triggered_by_user,
        )

        try:
            docs = await self.sampler.sample(
                kb_id=kb_id, sample_size=cfg.sample_size,
            )
            if not docs:
                await self.runs.complete(
                    run_id, status="completed",
                    docs_scanned=0, candidates_found=0, llm_calls=0,
                )
                return run_id

            existing_nodes = await self.candidates.list_approved_labels(
                kb_id, "node",
            )
            existing_rels = await self.candidates.list_approved_labels(
                kb_id, "relationship",
            )

            candidates_found = 0
            llm_calls = 0
            for i in range(0, len(docs), cfg.batch_size):
                batch = docs[i : i + cfg.batch_size]
                prompt = SCHEMA_DISCOVERY_PROMPT.format(
                    kb_id=kb_id,
                    n=len(batch),
                    existing_nodes=", ".join(existing_nodes) or "(none)",
                    existing_rels=", ".join(existing_rels) or "(none)",
                    docs="\n\n---\n\n".join(
                        f"[doc {j+1}] {d['content'][:cfg.doc_preview_chars]}"
                        for j, d in enumerate(batch)
                    ),
                )
                try:
                    raw = self.llm.invoke(document="", prompt_template=prompt)
                    llm_calls += 1
                    response = parse_discovery_response(raw)
                except (RuntimeError, ValueError) as exc:
                    logger.warning(
                        "Bootstrap batch %d failed (kb=%s): %s",
                        i // cfg.batch_size, kb_id, exc,
                    )
                    continue

                candidates_found += await self._upsert_candidates(
                    kb_id, response, cfg,
                )

            await self.runs.complete(
                run_id, status="completed",
                docs_scanned=len(docs),
                candidates_found=candidates_found,
                llm_calls=llm_calls,
            )
            return run_id

        except Exception as exc:  # noqa: BLE001 — fail-record-then-reraise
            logger.exception("Bootstrap failed for %s", kb_id)
            await self.runs.complete(
                run_id, status="failed", error_message=str(exc),
            )
            raise

    async def _upsert_candidates(
        self,
        kb_id: str,
        response: DiscoveryResponse,
        cfg: BootstrapConfig,
    ) -> int:
        count = 0
        for cand in response.node_candidates:
            if cand.confidence < cfg.confidence_threshold:
                continue
            await self.candidates.upsert(
                kb_id=kb_id, candidate_type="node", label=cand.label,
                confidence=cand.confidence,
                examples=[{"sample": ex} for ex in cand.examples],
            )
            count += 1
        for cand in response.relation_candidates:
            if cand.confidence < cfg.confidence_threshold:
                continue
            await self.candidates.upsert(
                kb_id=kb_id, candidate_type="relationship",
                label=cand.label, confidence=cand.confidence,
                source_label=cand.source or None,
                target_label=cand.target or None,
                examples=[{"sample": ex} for ex in cand.examples],
            )
            count += 1
        return count


__all__ = [
    "BootstrapAlreadyRunning",
    "BootstrapConfig",
    "CandidateRepoProto",
    "DocSampler",
    "LLMClient",
    "RunRepoProto",
    "SchemaBootstrapper",
]
