# RBAC & Multi-Tenancy (B-0)

axiomedge 의 인증/권한/멀티테넌시 가이드. 프론트 (Next.js web/admin) 는
이 문서가 정의한 모델 위에서만 동작해야 함.

## 핵심 결정

1. **Multi-tenant 모델: KB-as-tenant-boundary** — 모든 KB 는 정확히 하나의
   `organization_id` 에 속하고, KB 자식 (Document/Chunk/Glossary/Trust 등)
   은 KB 를 통해 자동 격리된다. 따라서 24개 자식 모델에 `organization_id`
   컬럼을 추가하지 않는다.
2. **단일 SSOT: middleware-level enforcement** — 인증과 권한 매트릭스 둘 다
   `AuthMiddleware` 가 강제한다. 라우트 핸들러에서 `Depends(require_*)` 를
   인라인하지 않는다 (audit 가능성 + 누락 방지).
3. **404 ≫ 403 (cross-tenant)** — 다른 org 의 리소스 조회 시도는 404 로
   응답한다. 존재 자체를 leak 하지 않는다.

## Roles

| Role | weight | 의미 |
|---|---|---|
| `OWNER` | 9000 | Org 소유자 — billing/settings/destroy |
| `ADMIN` | 3000 | Org 관리자 — user/role/data source 관리 |
| `MEMBER` | 2000 | 일반 사용자 — KB 읽기/쓰기, 글로서리 기여 |
| `VIEWER` | 1000 | 읽기 전용 |

기존 5개 legacy role (`viewer / contributor / editor / kb_manager / admin`)
은 `is_legacy=True` 플래그로 보존되며 weight ≤ 100 — canonical 4개와
명확히 분리. 신규 role 할당은 항상 canonical 사용. 마이그레이션 매핑은
`MIGRATION_GUIDE.md` 참조.

## Permission Matrix

| Resource | Action | OWNER | ADMIN | MEMBER | VIEWER |
|---|---|:-:|:-:|:-:|:-:|
| `org` | `manage` (settings/billing/destroy) | ✅ | ❌ | ❌ | ❌ |
| `org:user` | `manage` (invite/remove/role) | ✅ | ✅ | ❌ | ❌ |
| `kb` | `create` | ✅ | ✅ | ❌ | ❌ |
| `kb` | `delete` | ✅ | ✅ | ❌ | ❌ |
| `kb` | `read` | ✅ | ✅ | ✅ | ✅ |
| `kb` | `write` (config/owner) | ✅ | ✅ | KB-scope | ❌ |
| `document` | `read` / `search` | ✅ | ✅ | ✅ | ✅ |
| `document` | `write` (ingest/upload) | ✅ | ✅ | ✅ | ❌ |
| `document` | `delete` | ✅ | ✅ | KB-scope | ❌ |
| `glossary` | `read` | ✅ | ✅ | ✅ | ✅ |
| `glossary` | `write` | ✅ | ✅ | ✅ | ❌ |
| `feedback` | `submit` | ✅ | ✅ | ✅ | ✅ |
| `quality` | `read` (eval scores) | ✅ | ✅ | ✅ | ❌ |
| `quality` | `write` (golden set) | ✅ | ✅ | ❌ | ❌ |
| `agentic` | `ask` | ✅ | ✅ | ✅ | ✅ |
| `data_source` | `manage` | ✅ | ✅ | ❌ | ❌ |
| `distill` | `manage` | ✅ | ✅ | ❌ | ❌ |
| `audit_log` | `read` | ✅ | ✅ | ❌ | ❌ |

자세한 endpoint→permission 매핑은 `src/auth/permission_matrix.py` 의
`PERMISSION_RULES` 가 단일 SSOT. 새 라우트 추가 시 여기에 규칙 추가.

## Org Context Resolution

JWT access token 에 `active_org_id` claim 이 포함된다.
`get_current_org` dependency 가 다음 우선순위로 결정:

1. `X-Organization-Id` 헤더 (org switcher)
2. JWT `active_org_id` claim
3. 단일 멤버십 자동 선택
4. 다중 멤버십 + 미선택 → **409 Conflict** (`POST /api/v1/auth/switch-org` 또는 헤더로 선택 필요)
5. 멤버십 없음 → **403 Forbidden**

`AUTH_ENABLED=false` (dev) 시에는 anonymous OWNER + `default-org` 자동 부여
— 로컬 Streamlit + 기존 테스트 호환.

## Middleware Enforcement Flow

```
Request
  ├─ public-path 화이트리스트 (health/docs/login/register/refresh) → bypass
  ├─ AUTH_ENABLED=false → anonymous user attach → bypass
  ├─ Bearer/Cookie 토큰 추출 → auth_provider.verify_token()
  │   ├─ 실패 → 401
  │   └─ 성공 → request.state.auth_user 캐시
  ├─ permission_matrix.find_required_permission(method, path)
  │   ├─ 매칭 X → pass (auth-only)
  │   ├─ self-introspect 센티넬 → pass (auth-only)
  │   └─ (resource, action) → rbac_engine.check_permission(roles, ...)
  │       ├─ allowed → call_next
  │       └─ denied → 403
  └─ route handler
      └─ get_current_org dependency → org-scoped queries
```

## Cross-Tenant 격리 보장

| 레이어 | 메커니즘 |
|---|---|
| **Postgres (KB)** | `KBConfigModel.organization_id` (NOT NULL + FK + 인덱스). repository 의 모든 read/list/update/delete 가 `organization_id` 키워드 인자 받아 WHERE 필터 |
| **Postgres (자식)** | KB-as-tenant-boundary — 자식 데이터는 `kb_id` 통해 접근 가능한 KB 목록으로만 격리 (자식 모델 자체에는 org 컬럼 없음) |
| **Qdrant** | KB 별 분리된 collection (`kb_<kb_id>`). KB 접근 차단되면 collection 접근도 자동 차단 |
| **Neo4j** | `find_related_chunks(scope_kb_ids=collections)` — collections 는 항상 org-필터 통과한 KB 만 |
| **Cache** | `get_active_kb_ids` 캐시 키에 `(registry_id, organization_id)` 튜플 — cross-tenant 캐시 leak 0 |

## API Surface 변경 (B-0)

| Endpoint | 변경 |
|---|---|
| `POST /api/v1/auth/login` | 응답에 `active_org_id` 포함 |
| `POST /api/v1/auth/refresh` | 새 토큰에 `active_org_id` 보존 |
| 모든 protected endpoint | 401 (no token) / 403 (lack permission) / 404 (foreign tenant) |
| Header `X-Organization-Id` | 다중 org user 가 컨텍스트 명시 |

## 마이그레이션 절차

신규 환경:
```bash
make db-init                      # alembic upgrade head + 자동 default-org 시드
```

기존 환경 (B-0 이전 데이터):
```bash
uv run alembic upgrade head       # 0002, 0003, 0004 순차 적용
uv run python scripts/backfill_org_id.py --apply
                                  # null organization_id → default-org
```

## 테스트

| 레벨 | 파일 | 무엇을 검증 |
|---|---|---|
| Unit | `tests/unit/test_auth_rbac.py` | role weight + permission matrix |
| Unit | `tests/unit/test_org_context.py` | get_current_org 우선순위 |
| Unit | `tests/unit/test_kb_org_filter.py` | repo WHERE-clause 8 케이스 |
| Unit | `tests/unit/test_permission_matrix.py` | endpoint → (resource, action) 룩업 |
| Unit | `tests/unit/test_auth_middleware_perm.py` | 4 role × 16 endpoint |
| Unit | `tests/unit/test_cross_tenant_unit.py` | 검색/그래프 격리 |
| Integration | `tests/integration/test_auth_enforcement.py` | 모든 protected route 401 |
| Integration | `tests/integration/test_cross_tenant.py` | 실 API 2-user cross-tenant |

## 관련 코드

- `src/auth/rbac.py` — CANONICAL_ROLES, LEGACY_ROLES, RBACEngine
- `src/auth/permission_matrix.py` — PERMISSION_RULES (SSOT)
- `src/auth/middleware.py` — AuthMiddleware (인증 + 권한 enforcement)
- `src/auth/dependencies.py` — get_current_user, get_current_org, require_kb_access
- `src/auth/org_service.py` — OrgService (멤버십 관리)
- `src/auth/jwt_service.py` — active_org_id claim
- `src/stores/postgres/repositories/kb_registry.py` — org-filtered KB queries
