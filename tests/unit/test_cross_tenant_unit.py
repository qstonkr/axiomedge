"""Unit-level cross-tenant isolation guarantees (B-0 Day 5).

Validates the *contracts* the search/graph layers rely on for tenant
separation, without needing live Postgres/Qdrant/Neo4j. Integration
counterparts (live API + 2 orgs) live in tests/integration/test_cross_tenant.py.

The structural claim being tested:
1. ``_step_resolve_collections`` propagates ``organization_id`` to the
   active-KB filter, so foreign-tenant collection names get dropped.
2. ``_step_graph_expand`` calls Neo4j with the *same* org-filtered
   collections list (not the raw qdrant catalog) — so graph hops cannot
   reach into other tenants' KBs.
3. The ``get_active_kb_ids`` cache is keyed by ``(registry, org_id)`` so
   org A's cached set never resolves for an org B request.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_resolve_collections_drops_other_org_kbs() -> None:
    """Qdrant catalog has 3 KBs; registry says only 1 belongs to caller's org.

    The unfiltered list comes from ``_resolve_collections_from_qdrant``
    (everything Qdrant has). After ``_filter_by_kb_registry`` only the org's
    KB should survive.
    """
    from src.api.routes._search_preprocess import _step_resolve_collections
    from src.api.routes import search_helpers

    # Active-KB cache must be empty — share-key bug guard.
    search_helpers._kb_registry_cache.clear()

    request = MagicMock()
    request.kb_ids = None
    request.kb_filter = None
    request.group_id = None
    request.group_name = None

    qcoll = AsyncMock()
    qcoll.get_existing_collection_names = AsyncMock(
        return_value=["kb_owned", "kb_other_org", "kb_third"]
    )

    registry = AsyncMock()
    # Org A only sees kb_owned (translated from collection-name format).
    registry.list_all = AsyncMock(
        return_value=[{"kb_id": "owned", "status": "active"}]
    )

    state = {
        "qdrant_collections": qcoll,
        "kb_registry": registry,
    }

    collections = await _step_resolve_collections(
        request, state, organization_id="org-A",
    )

    # Foreign org collections must be dropped; only owned remains.
    assert collections == ["owned"]
    # Cache must be partitioned by org so org-B traffic doesn't reuse this.
    assert ("/", "org-A") not in search_helpers._kb_registry_cache  # sanity
    cache_keys = list(search_helpers._kb_registry_cache.keys())
    assert any(k[1] == "org-A" for k in cache_keys), cache_keys


@pytest.mark.asyncio
async def test_get_active_kb_ids_partitioned_by_org() -> None:
    """Two orgs hitting the same registry instance must not share cache rows."""
    from src.api.routes import search_helpers
    from src.api.routes.search_helpers import get_active_kb_ids

    search_helpers._kb_registry_cache.clear()

    registry = AsyncMock()

    async def _fake_list(limit=100, offset=0, organization_id=None):
        if organization_id == "org-A":
            return [{"kb_id": "kb-A1", "status": "active"}]
        if organization_id == "org-B":
            return [{"kb_id": "kb-B1", "status": "active"}]
        return []

    registry.list_all = AsyncMock(side_effect=_fake_list)

    a_ids = await get_active_kb_ids(registry, organization_id="org-A")
    b_ids = await get_active_kb_ids(registry, organization_id="org-B")

    assert a_ids == {"kb-A1"}
    assert b_ids == {"kb-B1"}
    assert a_ids.isdisjoint(b_ids)
    # Two distinct cache slots — not one shared with org leak.
    assert len(search_helpers._kb_registry_cache) == 2


@pytest.mark.asyncio
async def test_graph_expand_uses_org_filtered_collections() -> None:
    """Neo4j graph hops must be limited to the same org-filtered KB list."""
    from src.api.routes._search_retrieve import _step_graph_expand

    captured: dict[str, Any] = {}

    expander = MagicMock()

    async def _expand(display_query, chunks, scope_kb_ids):
        captured["scope_kb_ids"] = scope_kb_ids
        result = MagicMock()
        result.expanded_source_uris = []
        result.graph_related_count = 0
        result.expanded_chunks = chunks
        return result

    expander.expand_with_entities = AsyncMock(side_effect=_expand)
    expander.expand = AsyncMock(side_effect=_expand)

    chunks = [{"id": "c1", "kb_id": "kb-A1"}]
    state = {"graph_expander": expander}

    await _step_graph_expand(
        display_query="x", all_chunks=chunks,
        collections=["kb-A1"],  # org-filtered list from _step_resolve_collections
        state=state, qdrant_url="http://q",
    )

    # Graph layer must have been called with the same org-scoped KB list
    # — not, e.g., a hard-coded "all KBs".
    assert captured["scope_kb_ids"] == ["kb-A1"]


def test_qdrant_collection_naming_isolates_kb() -> None:
    """KB-as-tenant-boundary contract — each KB has its own Qdrant collection.

    This is the structural reason cross-tenant Qdrant payload filters are
    unnecessary: there's no shared collection to leak from.
    """
    from src.stores.qdrant.collections import QdrantCollectionManager

    provider = MagicMock()
    provider.config.collection_name_overrides = {}
    provider.config.collection_prefix = "kb"
    mgr = QdrantCollectionManager(provider)

    a = mgr.get_collection_name("kb-org-a-private")
    b = mgr.get_collection_name("kb-org-b-private")

    assert a != b
    assert a == "kb_kb_org_a_private"
    assert b == "kb_kb_org_b_private"
