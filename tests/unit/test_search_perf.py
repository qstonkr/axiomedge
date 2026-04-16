"""Performance-blocker fixes (PR3) — regression + behaviour tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# KB registry TTL cache
# ---------------------------------------------------------------------------


class TestKBRegistryCache:
    """src/api/routes/search_helpers.py::get_active_kb_ids"""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self):
        from src.api.routes import search_helpers as helpers

        helpers._kb_registry_cache.clear()
        registry = MagicMock()
        registry.list_all = AsyncMock(
            return_value=[
                {"kb_id": "kb-a", "status": "active"},
                {"kb_id": "kb-b", "status": "active"},
                {"kb_id": "kb-c", "status": "archived"},
            ],
        )

        first = await helpers.get_active_kb_ids(registry)
        second = await helpers.get_active_kb_ids(registry)

        assert first == {"kb-a", "kb-b"}
        assert second == {"kb-a", "kb-b"}
        # Only one DB call despite two requests — cache hit
        assert registry.list_all.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self, monkeypatch):
        from src.api.routes import search_helpers as helpers

        helpers._kb_registry_cache.clear()
        registry = MagicMock()
        registry.list_all = AsyncMock(
            return_value=[{"kb_id": "kb-a", "status": "active"}],
        )

        # Freeze time around the cache boundary
        fake_now = [1000.0]
        monkeypatch.setattr(helpers.time, "monotonic", lambda: fake_now[0])

        await helpers.get_active_kb_ids(registry)
        # Advance past TTL
        fake_now[0] += helpers._KB_REGISTRY_CACHE_TTL_S + 1
        await helpers.get_active_kb_ids(registry)

        assert registry.list_all.await_count == 2

    @pytest.mark.asyncio
    async def test_only_active_kbs_returned(self):
        from src.api.routes import search_helpers as helpers

        helpers._kb_registry_cache.clear()
        registry = MagicMock()
        registry.list_all = AsyncMock(
            return_value=[
                {"kb_id": "active-1", "status": "active"},
                {"kb_id": "archived-1", "status": "archived"},
                {"kb_id": "draft-1", "status": "draft"},
                {"kb_id": "active-2", "status": "active"},
            ],
        )
        result = await helpers.get_active_kb_ids(registry)
        assert result == {"active-1", "active-2"}


# ---------------------------------------------------------------------------
# Multi-hop search parallelism
# ---------------------------------------------------------------------------


class TestMultiHopParallel:
    """src/graph/multi_hop_searcher.py::MultiHopSearcher.find_related"""

    @pytest.mark.asyncio
    async def test_queries_run_concurrently(self):
        """All entity queries should be awaited concurrently, not serially."""
        from src.graph.multi_hop_searcher import MultiHopSearcher

        call_order: list[str] = []
        call_sleeps: list[float] = []

        async def fake_execute_query(cypher: str, params: dict) -> list[dict]:
            call_order.append(f"start:{params['doc_id']}")
            # Each call sleeps a bit — if serial, total ≈ n * delay;
            # if parallel, total ≈ single delay.
            await asyncio.sleep(0.05)
            call_sleeps.append(0.05)
            call_order.append(f"end:{params['doc_id']}")
            return [
                {
                    "id": f"id-{params['doc_id']}",
                    "name": params["doc_id"],
                    "type": "Concept",
                    "distance": 1,
                    "relation_types": ["RELATED_TO"],
                    "properties": {},
                },
            ]

        neo4j = MagicMock()
        neo4j.execute_query = AsyncMock(side_effect=fake_execute_query)

        searcher = MultiHopSearcher(neo4j_client=neo4j)
        # Bypass the client/entity resolver path by calling with raw entity names.
        # The code falls back to entity_names[:5] loop when entity_ids path fails.
        import time
        start = time.monotonic()
        results = await searcher.find_related(
            entity_names=["e1", "e2", "e3", "e4", "e5"],
            max_hops=2,
            max_results=10,
        )
        elapsed = time.monotonic() - start

        # 5 calls ran — count confirms all were dispatched
        assert neo4j.execute_query.await_count == 5
        # Parallel execution: all 5 "start" events should happen before any "end"
        start_events = [e for e in call_order if e.startswith("start:")]
        assert start_events[:5] == [f"start:e{i}" for i in range(1, 6)]
        # Serial would be 5 × 0.05 = 0.25s; parallel should be ~0.05s.
        assert elapsed < 0.20, f"Execution was serial (took {elapsed:.3f}s)"
        # Results should contain all 5 unique nodes
        result_ids = {r.id for r in results}
        assert result_ids == {f"id-e{i}" for i in range(1, 6)}

    @pytest.mark.asyncio
    async def test_single_failure_does_not_block_others(self):
        from src.graph.multi_hop_searcher import MultiHopSearcher

        call_count = [0]

        async def fake_execute_query(cypher: str, params: dict) -> list[dict]:
            call_count[0] += 1
            if params["doc_id"] == "bad":
                raise RuntimeError("neo4j timeout")
            return [
                {
                    "id": f"id-{params['doc_id']}",
                    "name": params["doc_id"],
                    "type": "Concept",
                    "distance": 1,
                    "relation_types": [],
                    "properties": {},
                },
            ]

        neo4j = MagicMock()
        neo4j.execute_query = AsyncMock(side_effect=fake_execute_query)

        searcher = MultiHopSearcher(neo4j_client=neo4j)
        results = await searcher.find_related(
            entity_names=["good1", "bad", "good2"],
            max_hops=2,
            max_results=10,
        )

        assert call_count[0] == 3
        result_ids = {r.id for r in results}
        # bad failed, good1/good2 survived
        assert "id-good1" in result_ids
        assert "id-good2" in result_ids
        assert "id-bad" not in result_ids
