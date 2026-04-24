"""End-to-end-ish: bootstrap → candidates → approve → YAML updated.

Stubs the LLM + DocSampler + repos so the full onboarding path can
execute without a live DB / Neo4j / LLM. Covers spec §5.7.
"""

from __future__ import annotations

import pytest
import yaml

from src.api.routes.graph_schema_helpers import merge_label_into_yaml
from src.pipelines.graphrag.schema_bootstrap import (
    BootstrapConfig,
    SchemaBootstrapper,
)


class _FakeLLM:
    """Deterministic LLM stub — returns two candidates for any prompt."""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, *, document: str, prompt_template: str) -> str:
        self.calls += 1
        return (
            '{"new_node_types": [\n'
            '  {"label": "Ticket", "confidence": 0.91, '
            '"examples": ["sample ticket"], "reason": "e2e"}\n'
            '], "new_relation_types": [\n'
            '  {"label": "ASSIGNED_TO", "confidence": 0.88, '
            '"source": "Person", "target": "Ticket", '
            '"examples": ["assigned"], "reason": "e2e"}\n'
            ']}'
        )


class _FakeSampler:
    async def sample(self, *, kb_id: str, sample_size: int):
        return [
            {
                "doc_id": f"d{i}",
                "content": f"doc {i} with tickets & people",
                "source_type": "confluence",
            }
            for i in range(min(sample_size, 3))
        ]


class _StubCandidateRepo:
    def __init__(self) -> None:
        self.upserts: list[dict] = []

    async def upsert(self, **kw) -> None:
        self.upserts.append(kw)

    async def list_approved_labels(self, kb_id: str, candidate_type: str):
        return []


class _StubRunRepo:
    def __init__(self) -> None:
        self.created_kwargs: dict | None = None
        self.completed_kwargs: dict | None = None

    async def has_running(self, kb_id: str) -> bool:
        return False

    async def create(self, **kw):
        from uuid import uuid4

        self.created_kwargs = kw
        return uuid4()

    async def complete(self, run_id, **kw) -> None:
        self.completed_kwargs = {"run_id": run_id, **kw}


@pytest.mark.asyncio
async def test_kb_onboarding_flow(tmp_path, monkeypatch):
    """Bootstrap → candidates → admin approve → YAML updated."""
    # 1. Bootstrap over 3 sample docs.
    candidate_repo = _StubCandidateRepo()
    run_repo = _StubRunRepo()
    bootstrapper = SchemaBootstrapper(
        llm=_FakeLLM(),
        candidate_repo=candidate_repo,
        run_repo=run_repo,
        sampler=_FakeSampler(),
    )
    await bootstrapper.run(
        kb_id="newkb",
        triggered_by="test",
        triggered_by_user="sys@e2e",
        config=BootstrapConfig(sample_size=3, batch_size=3),
    )

    assert run_repo.created_kwargs is not None
    assert run_repo.completed_kwargs is not None
    assert run_repo.completed_kwargs["status"] == "completed"
    assert run_repo.completed_kwargs["candidates_found"] == 2
    # 1 node + 1 relationship = 2 upserts.
    assert len(candidate_repo.upserts) == 2
    labels = {u["label"] for u in candidate_repo.upserts}
    assert labels == {"Ticket", "ASSIGNED_TO"}

    # 2. Admin approves both — merge into YAML.
    schema_dir = tmp_path / "graph_schemas"
    schema_dir.mkdir()
    monkeypatch.setattr(
        "src.api.routes.graph_schema_helpers._SCHEMA_DIR", schema_dir,
    )

    for u in candidate_repo.upserts:
        merge_label_into_yaml(
            kb_id=u["kb_id"],
            candidate_type=u["candidate_type"],
            label=u["label"],
            approved_by="admin@e2e",
        )

    # 3. Verify YAML now contains both labels.
    data = yaml.safe_load((schema_dir / "newkb.yaml").read_text())
    assert "Ticket" in data["nodes"]
    assert "ASSIGNED_TO" in data["relationships"]
    # version bumps once per merge: 0 → 1 (node) → 2 (rel)
    assert data["version"] == 2
    approved = data["_metadata"]["approved_candidates"]
    assert {e["label"] for e in approved} == {"Ticket", "ASSIGNED_TO"}
