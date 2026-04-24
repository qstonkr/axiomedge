# GraphRAG Schema Evolution — Design Spec

**Status**: Draft — pending user review
**Author**: park.sw@gsretail.com (via brainstorming session)
**Date**: 2026-04-24
**Supersedes**: Hardcoded `KB_SCHEMA_PROFILES` in `src/pipelines/graphrag/prompts.py`

---

## 1. Background & Problem

### Current state

`KB_SCHEMA_PROFILES` (prompts.py:42) 는 6개 KB (`a-ari`, `g-espa`, `drp`,
`hax`, `itops_general`, `partnertalk`) 의 entity/relationship schema 를
**하드코딩**한 Python dict. 새 KB 추가 시 코드 PR + 배포 필요.

데이터 소스는 이미 20+ connector (Confluence, SharePoint, OneDrive, Jira,
Linear, Salesforce, GitHub Issues, Notion, 등) 를 지원하지만 **source_type
별 차이를 반영하지 않음** — Confluence wiki page 와 Jira issue 가 동일
prompt 로 처리됨.

### Problem drivers

1. **확장성 부재** — 새 KB / 새 source_type 추가 시 매번 코드 변경
2. **도메인 특성 미반영** — Jira 이슈에 "Issue/Sprint/Epic" 같은 타입이
   뽑혀야 하는데 generic "Person/Team/Topic" 으로 처리
3. **운영 경직성** — schema 수정이 release cycle 에 묶임 (hot-fix 불가)
4. **Long-tail 도메인 대응 불가** — 개발자가 미리 알기 어려운 도메인별
   entity 타입 (예: 매출성과, 경쟁점, Meeting) 이 놓침

### Goals

- **확장성**: 새 KB / source_type 추가 시 YAML 편집만으로 schema 적용
- **자동 발견**: 데이터가 보유한 실제 패턴을 LLM 이 배치로 발견, 사람이
  검증 후 반영
- **PROD 완성도**: 안전성 / 관찰성 / 롤백 가능성을 단순성보다 우선
- **후방 호환**: 기존 `KB_SCHEMA_PROFILES` consumer 깨지지 않음

### Non-goals (이번 설계 scope 외)

- 실시간 schema 진화 (Graphiti-식 per-doc 호출) — 향후 옵트인 기능으로만
  검토, 기본 경로 아님
- 그래프 embedding 기반 schema-less 추출 — 확장성 결여로 제외
- 기존 Neo4j 데이터 일괄 재분류 — 신규 문서만 새 schema 적용 (Q5
  forward-only)
- 다국어 alias 자동 번역 (Korean / English label 통합) — Phase out-of-scope

---

## 2. Design Principles

**모든 설계 선택은 PROD 완성도 기준.** 단순성보다 안전성/관찰성/롤백 우선.

| 원칙 | 실천 방법 |
|---|---|
| Fail-closed | LLM/parse 실패 시 schema 변경 없음, generic fallback |
| Source of truth | YAML (git 추적 + diff review 가능) |
| Human gate | Auto-approve 없음, admin 수동 승인 필수 |
| Rollback 명시 | `git revert` + `re-extract` trigger 조합 |
| Observability | 모든 주요 경로 Prometheus metric + audit log |
| Forward-only | 신규 문서만 새 schema, 기존 데이터는 on-demand 재처리 |

---

## 3. Architecture

### 3.1 3-layer schema resolution

```
Ingestion: doc + {kb_id, source_type} → resolve_schema() → SchemaProfile
                                              │
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                     [A] YAML           [B] YAML            [D] YAML
                     kb-specific        bootstrap-approved  source-type
                     override           (merged into A)     default
                     (human edit)                           (packaged)

   우선순위: A > B > D > generic fallback
   nodes/rels = UNION, prompt_focus = 상위 layer (A) 우선
```

**주의**: B 는 approve 시점에 KB-level YAML 파일에 merge 되므로 런타임
resolver 는 A/B 구분 없이 `<kb_id>.yaml` 하나만 읽음.

### 3.2 2-tier registry (Q1 결정)

| Tier | 관리 주체 | 용도 |
|---|---|---|
| **Tier 1** | `src/stores/neo4j/node_registry.py::ALL_NODE_TYPES` (코드) | 인프라 label (Document/Section/Chunk/Attachment/Table/CodeBlock) — 고정 |
| **Tier 2** | `deploy/config/graph_schemas/*.yaml` (config) | 도메인 label (Person/Store/Issue 등) — B 발견 + admin 승인 |

**근거**: 인프라 label 은 stable 해야 하므로 동적 변경 금지. 도메인 label
은 대부분 `(label, id) UNIQUE` 만 필요 (세밀한 index 는 YAML override).

### 3.3 Bootstrap (B) workflow

```
arq cron (daily) / KB 생성 / admin trigger
         │
         ▼
   sample docs (stratified by source_type + random fallback)
         │
         ▼
   LLM: SCHEMA_DISCOVERY_PROMPT (batch of 10 docs)
         │
         ▼
   graph_schema_candidates upsert (frequency/confidence)
         │
         ▼
   embedding similarity → similar label 제안 (자동 merge 안 함)
         │
         ▼
   Admin UI: pending candidates 검토
         │
         ▼
   Approve → YAML auto-commit (git bot PR)
         │
         ▼
   YAML mtime 변경 → runtime hot-reload
         │
         ▼
   (optional) Admin "Re-extract" → 해당 KB 전체 재처리 arq job
```

---

## 4. Data Model

### 4.1 YAML files

**경로 구조:**

```
deploy/config/graph_schemas/
├── _defaults/                    ← D layer
│   ├── confluence.yaml
│   ├── jira.yaml
│   ├── salesforce.yaml
│   ├── sharepoint.yaml / onedrive.yaml / google_drive.yaml / dropbox.yaml / box.yaml
│   ├── linear.yaml / asana.yaml / github_issues.yaml / notion.yaml
│   ├── gmail.yaml / outlook.yaml / teams.yaml
│   ├── file_upload.yaml / crawl_result.yaml
│   └── _generic.yaml             ← ultimate fallback
└── <kb_id>.yaml                  ← A layer + B approved (merged)
    └── g-espa.yaml / a-ari.yaml / drp.yaml / hax.yaml / itops_general.yaml / partnertalk.yaml
```

### 4.2 YAML schema (validated by pydantic)

```yaml
version: 2                        # int, increments on each approve
kb_id: g-espa                     # optional for KB-level; omit for _defaults
prompt_focus: >
  점포(GS25/CU), 경영주/OFC, ESPA활동/개선활동, 상품카테고리,
  지역/상권, 매출성과, 경쟁점

nodes:
  - Store
  - Person
  - Process
  - Meeting                       # ← B 가 발견 + admin approve

relationships:
  - MANAGES
  - OPERATES
  - SCHEDULED_IN                  # ← B 발견

indexes:                          # optional — Tier 2 label 별 custom index
  Meeting:
    - property: scheduled_at
      index_type: btree

options:
  disable_bootstrap: false        # true 면 B 가 이 KB skip
  schema_evolution: batch         # batch | realtime (default batch)
  bootstrap_sample_size: 100

_metadata:                        # 자동 생성, 수동 편집 금지
  last_approved_at: "2026-05-12T14:22:00Z"
  last_approved_by: "park.sw@gsretail.com"
  approved_candidates:
    - { label: "Meeting", type: "node", version_added: 2 }
```

### 4.3 DB tables (transient workspace only)

```sql
CREATE TABLE graph_schema_candidates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id           VARCHAR(64) NOT NULL,
    candidate_type  VARCHAR(16) NOT NULL CHECK (candidate_type IN ('node','relationship')),
    label           VARCHAR(64) NOT NULL,
    frequency       INT NOT NULL DEFAULT 1,
    confidence_avg  FLOAT NOT NULL,
    confidence_min  FLOAT NOT NULL,
    confidence_max  FLOAT NOT NULL,
    source_label    VARCHAR(64),             -- rel 의 source
    target_label    VARCHAR(64),
    examples        JSONB NOT NULL DEFAULT '[]',
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected','merged')),
    merged_into     VARCHAR(64),
    rejected_reason TEXT,
    similar_labels  JSONB DEFAULT '[]',
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ,
    decided_by      VARCHAR(128),
    UNIQUE(kb_id, candidate_type, label)
);
CREATE INDEX idx_candidates_kb_status ON graph_schema_candidates (kb_id, status, frequency DESC);
CREATE INDEX idx_candidates_pending ON graph_schema_candidates (status, last_seen_at DESC)
    WHERE status = 'pending';

CREATE TABLE graph_schema_bootstrap_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id           VARCHAR(64) NOT NULL,
    status          VARCHAR(16) NOT NULL
                    CHECK (status IN ('running','completed','failed','cancelled')),
    triggered_by    VARCHAR(32) NOT NULL
                    CHECK (triggered_by IN ('cron','kb_create','manual','volume_threshold')),
    triggered_by_user VARCHAR(128),
    sample_size     INT NOT NULL,
    sample_strategy VARCHAR(16) NOT NULL,
    docs_scanned    INT DEFAULT 0,
    candidates_found INT DEFAULT 0,
    llm_calls       INT DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    duration_ms     INT
);
CREATE INDEX idx_bootstrap_kb_time ON graph_schema_bootstrap_runs (kb_id, started_at DESC);
CREATE INDEX idx_bootstrap_running ON graph_schema_bootstrap_runs (kb_id)
    WHERE status = 'running';

CREATE TABLE graph_schema_reextract_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id           VARCHAR(64) NOT NULL,
    triggered_by_user VARCHAR(128) NOT NULL,
    schema_version_from INT NOT NULL,
    schema_version_to   INT NOT NULL,
    status          VARCHAR(16) NOT NULL
                    CHECK (status IN ('queued','running','completed','failed','cancelled')),
    docs_total      INT,
    docs_processed  INT DEFAULT 0,
    docs_failed     INT DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_reextract_kb ON graph_schema_reextract_jobs (kb_id, queued_at DESC);
```

### 4.4 Python dataclass

```python
# src/pipelines/graphrag/schema_types.py
@dataclass(frozen=True)
class IndexSpec:
    property: str
    index_type: Literal["btree", "fulltext", "range"] = "btree"

@dataclass(frozen=True)
class SchemaOptions:
    disable_bootstrap: bool = False
    schema_evolution: Literal["batch", "realtime"] = "batch"
    bootstrap_sample_size: int = 100

@dataclass(frozen=True)
class SchemaProfile:
    nodes: tuple[str, ...]
    relationships: tuple[str, ...]
    prompt_focus: str
    indexes: dict[str, tuple[IndexSpec, ...]] = field(default_factory=dict)
    options: SchemaOptions = field(default_factory=SchemaOptions)
    version: int = 1
    source_layers: tuple[str, ...] = field(default_factory=tuple)
```

---

## 5. User Experience

### 5.1 Personas

| Persona | Pain Points |
|---|---|
| Knowledge Admin | 20+ candidates 일괄 검토, 근거 불명확한 승인 |
| Domain Expert (KB 담당) | YAML/git 기술 장벽 |
| End User (검색) | schema 변경 중 결과 일관성 없음 |
| Developer | 디버깅 경로 부재 |
| Ops | silent cron failure, 비용 추적 |

### 5.2 Admin review UI (`/admin/graph-schema/candidates`)

- **Bulk actions** — "confidence ≥0.9 & freq ≥10" 일괄 approve
- **Context-rich cards** — label + frequency + confidence + 실제 examples + 원본 doc link
- **Similar-label merge suggestion** — embedding cosine ≥ 0.7
- **Impact preview** — approve 전 예상 신규 엔티티 개수 + re-extract 비용
- **Korean alias** — UI 검색용 display name
- **Rename / Merge / Reject** — 개별 판단 옵션

### 5.3 Domain expert — YAML 장벽 제거

Admin UI 가 common flow 100% 커버:
- Approve/Reject/Merge/Rename — UI
- `schema_evolution` flag — UI 체크박스
- `indexes` override — UI property 선택
- YAML 직접 편집은 advanced menu (고급 사용자만)

**YAML auto-PR flow:**
```
Admin approve → backend write YAML atomic (temp + rename)
→ git commit (bot user) → git push → gh pr create
→ reviewer 승인 → merge → main pull → runtime hot-reload
```

### 5.4 End user — transparency

- 검색 결과 배너: "🆕 Recently updated schema · 일부 오래된 문서는 새 개념을
  포함 안 할 수 있음"
- Re-extract 진행률 공개 (`/status/kb/<kb_id>`)

### 5.5 Developer — CLI

```bash
make graph-schema-scaffold source=sharepoint   # YAML template 생성
make graph-schema-dry-run kb=g-espa            # bootstrap 예상 결과
make graph-extract-debug doc_id=conf-1234      # 추출 과정 전체 JSON
```

### 5.6 Ops — 관찰성

- `/admin/graph-schema/health` dashboard
- Prometheus metrics:
  ```
  graph_schema_bootstrap_runs_total{kb_id, status}
  graph_schema_bootstrap_duration_seconds{kb_id}
  graph_schema_candidates_pending{kb_id}
  graph_schema_llm_calls_total{kb_id, purpose}
  graph_schema_reextract_jobs_total{kb_id, status}
  graph_schema_yaml_hot_reload_total
  graph_schema_resolution_layer_used_total{layer}
  ```
- 알림 (Slack/Email):
  - Bootstrap 연속 3회 실패 → on-call
  - Pending candidates > 50 → admin
  - YAML PR 48h 미머지 → admin 리마인더

### 5.7 First-time KB onboarding flow

1. KB 생성 (기존)
2. Background: bootstrap 자동 (sample 50)
3. ~5분 후 admin UI 배지: "🟡 pending 15 candidates"
4. Admin 검토 → bulk approve + 개별 judgment
5. YAML PR 생성 → review → merge
6. Runtime hot-reload
7. (optional) "Re-extract" 버튼

**소요: ~10~15분 (초회)**

---

## 6. Component Implementation

### 6.1 신규 모듈

```
src/pipelines/graphrag/
├── schema_types.py          🆕 SchemaProfile / SchemaOptions / IndexSpec
├── schema_resolver.py       🆕 resolve_schema() + YAML loader + hot-reload
├── schema_bootstrap.py      🆕 SchemaBootstrapper + sampling + LLM discovery
├── schema_prompts.py        🆕 SCHEMA_DISCOVERY_PROMPT + strict parser
└── source_defaults.py       🆕 D layer YAML path mapping

src/stores/neo4j/
└── dynamic_schema.py        🆕 ensure_dynamic_constraints() + session cache

src/stores/postgres/repositories/
├── schema_candidate_repo.py 🆕
├── bootstrap_run_repo.py    🆕
└── reextract_job_repo.py    🆕

src/api/routes/
├── graph_schema.py          🆕 admin API
└── graph_schema_helpers.py  🆕 YAML writer + git commit + PR creator

src/jobs/
├── schema_bootstrap_cron.py 🆕 arq cron + on-demand
└── schema_reextract.py      🆕 arq re-extract job

src/cli/
└── graph_schema_cli.py      🆕 make graph-schema-* commands

deploy/config/graph_schemas/ 🆕 YAML 디렉터리
docs/
├── GRAPH_SCHEMA_EVOLUTION.md 🆕
└── GRAPH_SCHEMA_AUTHORING.md 🆕
```

### 6.2 `SchemaResolver` 핵심 로직

```python
# src/pipelines/graphrag/schema_resolver.py
class SchemaResolver:
    @staticmethod
    def resolve(*, kb_id: str | None, source_type: str | None) -> SchemaProfile:
        layers = []
        if source_type:
            d = SchemaResolver._load_source_default(source_type)
            if d: layers.append((f"D:{source_type}", d))
        if kb_id:
            a = SchemaResolver._load_kb_schema(kb_id)
            if a: layers.append((f"A:{kb_id}", a))
        if not layers:
            return SchemaResolver._generic_fallback()

        # Merge: UNION nodes/rels, A > D for prompt_focus/options
        nodes = set(); rels = set(); indexes = {}
        for _, p in layers:
            nodes.update(p.nodes); rels.update(p.relationships)
            for k, v in p.indexes.items(): indexes.setdefault(k, []).extend(v)
        return SchemaProfile(
            nodes=tuple(sorted(nodes)),
            relationships=tuple(sorted(rels)),
            prompt_focus=layers[-1][1].prompt_focus,
            indexes={k: tuple(v) for k, v in indexes.items()},
            options=layers[-1][1].options,
            version=layers[-1][1].version,
            source_layers=tuple(name for name, _ in layers),
        )
```

**mtime-based hot-reload** — `_cache_mtime` 비교로 파일 수정 시 자동 reload.
**Thread-safe** — `_cache_lock`. **Fail-closed** — YAML parse 실패 시
generic fallback + ERROR log.

### 6.3 `SchemaBootstrapper` 핵심 로직

```python
class SchemaBootstrapper:
    async def run(
        self, *, kb_id: str, triggered_by: str,
        triggered_by_user: str | None = None,
        config: BootstrapConfig | None = None,
    ) -> UUID:
        if await self.runs.has_running(kb_id):
            raise BootstrapAlreadyRunning(kb_id)
        if SchemaResolver.resolve(kb_id=kb_id, source_type=None).options.disable_bootstrap:
            raise BootstrapDisabled(kb_id)

        run_id = await self.runs.create(kb_id=kb_id, triggered_by=triggered_by, ...)
        try:
            docs = await self._sample_docs(kb_id, cfg)          # stratified
            existing_n, existing_r = self._existing_labels(schema)
            candidates, llm_calls = await self._discover(kb_id, docs, existing_n, existing_r)
            for cand in candidates:
                if cand.confidence < cfg.confidence_threshold: continue
                similar = await self._find_similar(kb_id, cand, cfg)
                await self.candidates.upsert(...)
            await self.runs.complete(run_id, status="completed", ...)
        except Exception as e:
            await self.runs.complete(run_id, status="failed", error_message=str(e))
            raise
        return run_id
```

**Sampling (Q3 결정):** stratified by source_type, `per_source = max(5, N/len(sources))`.
단일 source KB 는 random fallback.

### 6.4 Dynamic Neo4j constraint

```python
# src/stores/neo4j/dynamic_schema.py
_SAFE_LABEL = re.compile(r"^[A-Z][a-zA-Z0-9_]{0,63}$")
_applied_labels: set[str] = set()  # session cache

async def ensure_dynamic_constraints(client, schema: SchemaProfile) -> dict[str, int]:
    stats = {"created": 0, "skipped": 0, "failed": 0}
    async with _lock:
        for label in schema.nodes:
            if label in NODE_LABELS:        # Tier 1 — node_registry 관리
                stats["skipped"] += 1; continue
            if label in _applied_labels:
                stats["skipped"] += 1; continue
            if not _SAFE_LABEL.match(label):
                logger.error("Unsafe label rejected: %r", label)
                stats["failed"] += 1; continue
            try:
                await client.execute_write(
                    f"CREATE CONSTRAINT {label.lower()}_id_unique IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                )
                for spec in schema.indexes.get(label, ()):
                    # btree / fulltext 각각 CREATE INDEX/FULLTEXT INDEX IF NOT EXISTS
                    ...
                _applied_labels.add(label)
                stats["created"] += 1
            except NEO4J_FAILURE as e:  # 기존 convention (errors.py)
                logger.error("Constraint failed for %s: %s", label, e)
                stats["failed"] += 1
    return stats
```

### 6.5 Concurrency control (PROD)

3단계 방어:
1. **arq queue per-KB** — `queue_name=f"schema_bootstrap_{kb_id}"`
2. **DB `has_running` check** — `WHERE status='running' AND started_at > NOW() - INTERVAL '1 hour'`
3. **Stale cleanup cron** — daily, `status='running' > 1h` → mark `failed`

### 6.6 LLM prompts

**Bootstrap discovery** (`schema_prompts.py`):
```
다음은 KB "{kb_id}" 의 샘플 문서 {n}개입니다.
이 도메인의 지식 그래프에 적합한 신규 entity/relationship 타입을 제안하세요.

### 이미 확정된 타입 (중복 제안 금지)
- Entity: {existing_nodes}
- Relationship: {existing_rels}

### 판단 기준
- 문서 2개 이상에 등장하는 개념만 제안
- 기존 타입으로 충분히 커버되면 신규 제안 금지
- Confidence 0.0~1.0: 0.95=일관명확 / 0.85=약간모호 / 0.70=1~2문서만

### 샘플 문서
{docs}

### 출력 (JSON 만)
{{"new_node_types":[{{"label":"<CamelCase>","reason":"<한 문장>","confidence":0.92,
  "examples":["<원문 구절>"]}}],
  "new_relation_types":[{{"label":"<SCREAMING_SNAKE>","source":"<Entity>","target":"<Entity>",
  "reason":"<한 문장>","confidence":0.9,"examples":["<원문 구절>"]}}]}}
```

**Extraction** (수정된 `build_extraction_prompt`):
```python
def build_extraction_prompt(doc_text: str, schema: SchemaProfile) -> str:
    return f"""다음 문서에서 엔티티와 관계를 추출하세요.
허용된 Entity 타입 (밖 사용 금지): {", ".join(schema.nodes)}
허용된 Relationship 타입 (밖 사용 금지): {", ".join(schema.relationships)}
도메인 특성: {schema.prompt_focus}
문서: {doc_text}
JSON 으로만: {{"nodes":[...],"relationships":[...]}}"""
```

**LLM hallucination 방어:** extractor.py 가 schema outside label **silent drop**:
```python
result.nodes = [n for n in result.nodes if n.type in schema.nodes]
result.relationships = [r for r in result.relationships if r.type in schema.relationships]
```

### 6.7 Source-type injection 방어

`source_type` 은 user input (connector config) → injection 위험.
`_SAFE_SOURCE_TYPE` 화이트리스트 (YAML 파일 존재 기준) 로 pre-validate.

---

## 7. Backward Compatibility

### Legacy facade (`prompts.py`)

```python
class _LegacyProxy:
    _cache: dict[str, Any] | None = None
    def __getitem__(self, key):
        if self._cache is None: self._cache = _build_legacy_profiles()
        return self._cache[key]
    def __contains__(self, key): ...
    def get(self, key, default=None): ...

KB_SCHEMA_PROFILES = _LegacyProxy()  # 기존 import 경로 유지

def get_kb_schema(kb_id: str) -> dict:
    schema = SchemaResolver.resolve(kb_id=kb_id, source_type=None)
    return {"nodes": list(schema.nodes), "relationships": list(schema.relationships),
            "prompt_focus": schema.prompt_focus}

def build_extraction_prompt(doc_text: str, arg: str | SchemaProfile | None = None) -> str:
    schema = (arg if isinstance(arg, SchemaProfile)
              else SchemaResolver.resolve(kb_id=arg, source_type=None) if isinstance(arg, str)
              else SchemaResolver._generic_fallback())
    ...
```

**보장:** 기존 코드 (`KB_SCHEMA_PROFILES["g-espa"]["nodes"]`, `build_extraction_prompt(doc, kb_id)`) 모두 동작.

---

## 8. Migration Plan

### 8.1 이관 대상 3가지

| 대상 | 방법 |
|---|---|
| Code (KB_SCHEMA_PROFILES → YAML) | `scripts/ops/migrate_schema_to_yaml.py` 1회 실행 |
| YAML directory 생성 | `_generic.yaml` + source defaults 우선순위대로 |
| Neo4j 기존 데이터 | **이관 불필요** — 기존 label 그대로 유효, constraint `IF NOT EXISTS` idempotent |

### 8.2 Source-type default 작성 우선순위

1. Phase 1: `confluence`, `file_upload`, `crawl_result`, `_generic`
2. Phase 2: `jira`, `github_issues`, `linear`, `notion`
3. Phase 3: `sharepoint`, `onedrive`, `google_drive`, `dropbox`, `box`
4. Phase 4: `salesforce`, `asana`, `gmail`, `outlook`, `teams`

### 8.3 Rollback 시나리오

- 잘못 approve 된 label (e.g., "Meeting" 이 오판) → `git revert <yaml-commit>`
- Neo4j 에 이미 저장된 "Meeting" 노드 → 수동 cleanup
  ```
  make graph-drop-label label=Meeting    # DETACH DELETE + DROP CONSTRAINT
  ```
- Re-extract job 이 진행 중 → admin UI 에서 cancel 가능

---

## 9. Testing Strategy

### 9.1 Test layers

| Layer | 파일 | 대상 |
|---|---|---|
| Unit | `test_schema_resolver.py` | 3-layer merge 8가지 조합 + YAML parse 실패 |
| Unit | `test_schema_bootstrap.py` | sampling / LLM mock / candidate merge |
| Unit | `test_dynamic_constraint.py` | Tier 1 skip, injection 방어, idempotency |
| Unit | `test_schema_migration.py` | migrate script idempotent |
| Integration | `test_schema_end_to_end.py` | bootstrap → approve → YAML → hot-reload |
| Integration | `test_dynamic_constraint_neo4j.py` | real Neo4j CREATE CONSTRAINT + MERGE |
| E2E | `test_kb_onboarding.py` | KB 생성 → bootstrap → approve → re-extract |

### 9.2 핵심 regression 시나리오

1. Layer merge 순서 (A+D 에서 A 가 이김)
2. Invalid YAML fail-closed (generic fallback, 서비스 죽지 않음)
3. Concurrent bootstrap raise `BootstrapAlreadyRunning`
4. LLM hallucinate "FakeType" → silent drop
5. Cypher label injection reject
6. Tier 1/2 label 공존 (Person + Meeting)
7. Legacy compat (`KB_SCHEMA_PROFILES["g-espa"]`)
8. Hot-reload (재시작 없이 다음 ingestion 반영)

### 9.3 Coverage 목표

- 신규 파일: **≥ 85%** (critical path)
- 기존 파일 수정: PR6 gate 80% 유지

---

## 10. Implementation Phases

| Phase | 내용 | 기간 |
|---|---|---|
| 1 | YAML foundation + resolver + legacy facade + 6 KB 이관 | 4일 |
| 2 | Dynamic constraint + extractor 통합 | 3일 |
| 3 | Bootstrap DB + cron + LLM discovery | 1.5주 |
| 4 | Admin UI + YAML auto-commit | 1.5주 |
| 5 | Re-extract + Ops 도구 + 모니터링 | 1주 |
| 6 | (Optional) Realtime schema evolution | 1주 |

**총: 5~6주** (Phase 1~5). Minimum viable: Phase 1+2 ≈ 1.5주 (B 없이도 YAML
기반 schema 관리로 전환).

---

## 11. Risk Register

| Risk | 대응 | 담당 Phase |
|---|---|---|
| LLM 쓸모없는 label ("Stuff") | confidence/frequency threshold + admin review | 3 |
| 동의어 label (Employee/Staff) | embedding 유사도 제안 + 수동 merge | 3 |
| Neo4j constraint 생성 시 기존 data 위반 | `IF NOT EXISTS` + fail-open + alert | 2 |
| YAML 오타 | pydantic validation `^[A-Z][a-zA-Z_]*$` | 1 |
| YAML auto-commit write 권한 오용 | bot user + branch protection + PR review | 4 |
| Bootstrap drift (과다 추가) | 일 1회 audit — threshold 초과 시 alert | 5 |
| DB migration 실패 | rollback SQL + staged apply | 3 |
| Realtime 처리량 병목 | opt-in + throttling + batch fallback | 6 |

---

## 12. Open Questions (spec 확정 후 구현 단계에서 결정)

- embedding 유사도 임계값 초기 default (0.7 제안, 실제 데이터로 tune)
- Bootstrap LLM 샘플 수 (batch 10 docs 제안, token 한도 고려)
- YAML auto-commit 시 PR-based vs direct commit (PROD 기준 PR 권장, 단 반복적
  승인자용 "trusted merge" 옵션 고려)
- Admin UI 에서 Korean alias 표시 범위 (전체 vs 관리자 페이지만)

---

## 13. References

- Prior reviews in this session: code-reviewer agent 3회 (commit 36a0049, 7de0443,
  deddad7 각각)
- Current hardcoded schema: `src/pipelines/graphrag/prompts.py::KB_SCHEMA_PROFILES`
- Current Neo4j schema infra: `src/stores/neo4j/node_registry.py::ALL_NODE_TYPES`
- GraphRAG 전체 문서: `docs/GRAPHRAG.md`
- IMPROVEMENT_PLAN follow-up candidates: `docs/IMPROVEMENT_PLAN.md::Neo4j 안정성 follow-up`
