# GraphRAG Schema Evolution (Phase 1 landing)

**Status**: Phase 1 shipped — YAML-based schema foundation.
**Spec**: `docs/superpowers/specs/2026-04-24-graph-schema-evolution-design.md`

---

## What changed

The 6 hardcoded KB schemas that used to live in
`src/pipelines/graphrag/prompts.py::KB_SCHEMA_PROFILES` now live in YAML
files under `deploy/config/graph_schemas/`:

```
deploy/config/graph_schemas/
├── _defaults/                    ← source-type defaults (D layer)
│   ├── _generic.yaml
│   ├── confluence.yaml
│   ├── file_upload.yaml
│   └── crawl_result.yaml
├── a-ari.yaml                    ← KB overrides (A layer)
├── drp.yaml
├── g-espa.yaml
├── hax.yaml
├── itops_general.yaml
└── partnertalk.yaml
```

Existing callers (`KB_SCHEMA_PROFILES["g-espa"]`, `get_kb_schema(kb_id)`,
`build_extraction_prompt(doc, kb_id)`) continue to work unchanged — a
compatibility facade in `prompts.py` reads the YAML files lazily via
`SchemaResolver`.

New callers should prefer the resolver directly:

```python
from src.pipelines.graphrag import SchemaResolver

schema = SchemaResolver.resolve(kb_id="g-espa", source_type="confluence")
# schema.nodes, schema.relationships, schema.prompt_focus, schema.options
```

---

## Adding a new KB schema

1. Create `deploy/config/graph_schemas/<kb_id>.yaml` with the fields below.
2. Commit the file. Runtime picks it up on next ingestion (mtime-based
   hot-reload — no restart needed).

Minimum YAML shape:

```yaml
version: 1
kb_id: my-new-kb
prompt_focus: "문서의 도메인 특성 한 줄"
nodes:
  - Person
  - Topic
relationships:
  - MEMBER_OF
  - COVERS
options:
  disable_bootstrap: false
  schema_evolution: batch
  bootstrap_sample_size: 100
```

Parse errors log to `ERROR` level and fall back to the generic default
(no crash).

---

## Adding a new connector (source_type) default

1. Create `deploy/config/graph_schemas/_defaults/<source_type>.yaml` with
   the same shape as the KB file (omit `kb_id`).
2. The filename **must** be lowercase alphanumeric + underscore. That is
   also the whitelist for `is_valid_source_type()` (Cypher injection
   defense).

Available source defaults today: `_generic`, `confluence`, `file_upload`,
`crawl_result`. The rest of the connector catalog (jira, salesforce,
sharepoint, …) ships in Phase 1.5 per spec §8.2.

---

## What's next (future phases)

- **Phase 2**: schema-aware extraction prompt + Neo4j dynamic constraints.
- **Phase 3**: bootstrap discovery service + admin review workflow.
- **Phase 4+**: admin UI, re-extract, optional realtime evolution.

See the spec for detailed phase plan.
