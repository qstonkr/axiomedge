# axiomedge web (`src/apps/web/`)

Next.js 16 프론트엔드. **사용자 6 페이지 (B-1)** + **admin 17 라우트
(B-2)** 가 한 코드베이스에 공존하며 Streamlit dashboard 를 대체합니다.
자세한 디자인 시스템은 [`../src/apps/web/DESIGN.md`](../src/apps/web/DESIGN.md), 백엔드 RBAC 모델은
[`docs/RBAC.md`](RBAC.md) 참조.

## 어디에 뭐가 있나

```
src/apps/web/
├── DESIGN.md             # 디자인 시스템 SSOT (Linear+Notion 토큰)
├── Dockerfile            # multi-stage standalone runtime (Day 10)
├── next.config.ts        # output: "standalone"
├── package.json          # dev/build/test/lint/typecheck/gen:api scripts
├── playwright.config.ts  # E2E
├── vitest.config.ts      # unit/component
└── src/
    ├── app/
    │   ├── (auth)/login/    # 로그인 페이지
    │   ├── (app)/           # AuthGuard layout + 6 사용자 페이지
    │   │   ├── chat/, find-owner/, my-knowledge/,
    │   │   ├── my-documents/, my-feedback/, search-history/
    │   │   ├── error.tsx    # 페이지-레벨 error boundary
    │   │   └── loading.tsx  # Suspense skeleton
    │   ├── (admin)/         # AdminGuard layout + 17 admin 라우트 (B-2)
    │   │   ├── layout.tsx   # OWNER/ADMIN role 가드 + AdminShell
    │   │   └── admin/       # /admin (대시보드) + 17 sub-route
    │   ├── api/auth/        # BFF: login/logout/refresh/me/switch-org
    │   ├── api/proxy/[...path]/  # FastAPI 일반 proxy (cookie → Bearer)
    │   ├── layout.tsx       # 한국어 lang, Pretendard fallback
    │   ├── page.tsx         # 랜딩
    │   └── globals.css      # Tailwind v4 + design tokens (+[data-admin] 변형)
    ├── components/
    │   ├── ui/              # 10 primitives (Button/Card/Input/...)
    │   ├── layout/          # Sidebar, OrgSwitcher, ThemeToggle (사용자)
    │   ├── admin/           # AdminShell + 17 *Client + DataTable + 차트 (B-2)
    │   ├── chat/, find-owner/, my-feedback/,
    │   ├── my-documents/, search-history/, my-knowledge/
    │   └── providers.tsx    # NextIntl + TanStack Query + Toast
    ├── hooks/
    │   ├── use*.ts          # useSearch, useAgenticAsk, useMyKnowledge, ...
    │   └── admin/           # useAdminDashboard, useContent, useOps, ... (B-2)
    ├── lib/
    │   ├── api/             # types.ts (openapi-typescript) + client + endpoints
    │   ├── auth/session.ts  # server-only getSession()
    │   ├── query-client.tsx # TanStack Query setup
    │   └── useEscape.ts     # modal Esc-close hook
    ├── store/               # Zustand: chat, theme
    ├── i18n/                # ko / en messages + config
    └── proxy.ts             # Next.js 16 middleware (구 middleware.ts)
```

## 첫 실행 (30초)

```bash
# 한 번만 — pnpm 모듈 설치
make web-install

# FastAPI 백엔드 (다른 터미널)
make api                    # localhost:8000

# Next.js 개발 서버
make web-dev                # localhost:3000
```

`AUTH_ENABLED=false` (로컬 dev 기본) 에서는 anonymous admin 으로 자동
로그인 — 화면 6개 모두 바로 진입 가능. `AUTH_ENABLED=true` 로 테스트하려면
`make api` 환경 변수 + 시드 user 추가 후 `/login` 에서 로그인.

## 핵심 결정 (B-1 plan 발췌)

| 결정 | 선택 |
|---|---|
| Multi-tenant 모델 | KB-as-tenant-boundary (B-0 결정 그대로) |
| Personal KB | N개, cap 10, 가입 시 1개 자동, owner-only 격리 |
| DESIGN.md 톤 | Linear (productivity SaaS) + Notion (content-first elevation) |
| 인증 | BFF — Next.js route handler 가 FastAPI 프록시, JWT 는 HttpOnly cookie |
| 상태 관리 | TanStack Query (서버 ~80%) + Zustand (UI ~20%) |
| 스타일링 | Tailwind v4 + design tokens (`globals.css` 의 `@theme inline`) |
| API 타입 | openapi-typescript — FastAPI `/openapi.json` → 자동 생성 |
| i18n | next-intl, ko primary + en 스캐폴드 |
| 테스트 | Vitest + @testing-library + Playwright |
| 폴더 위치 | `src/apps/web/` (Streamlit `src/apps/dashboard/` 와 같은 라인) |

## 명령어 치트시트

| make 타겟 | 동작 |
|---|---|
| `make web-install` | pnpm install |
| `make web-dev` | next dev (localhost:3000) |
| `make web-build` | production build (.next/) |
| `make web-typecheck` | tsc --noEmit |
| `make web-lint` | eslint |
| `make web-test` | Vitest (unit/component) |
| `make web-test-e2e` | Playwright (자동 dev 서버 기동) |
| `make web-gen-api` | OpenAPI → src/lib/api/types.ts (uvicorn 가동 필요) |
| `make web-gen-api-offline` | 동일하지만 uvicorn 없이 — `scripts/dump_openapi.py` 사용 |
| `make web-docker-build` | `axiomedge-web:latest` 이미지 빌드 |
| `make web-docker-run` | 빌드한 이미지 실행 (`-e API_URL=...` 필요시) |

## BFF 인증 흐름

```
Browser → /api/auth/login                 (Next.js route handler)
        → FastAPI /api/v1/auth/login
        ← {access_token, refresh_token, active_org_id, user, roles}
        ← Set-Cookie: access_token=...; HttpOnly; SameSite=Lax
        ← Set-Cookie: refresh_token=...; HttpOnly; SameSite=Lax

Browser → /api/proxy/api/v1/<anything>    (모든 일반 요청)
        → 동일 cookie + 헤더 포워드
        → FastAPI /api/v1/<anything>      (Authorization: Bearer <cookie>)

401 응답:
  → API client 가 자동으로 /api/auth/refresh 1회 시도
  → 성공 → 원 요청 재시도
  → 실패 → window.location.href = "/login?next=..."
```

JWT 는 브라우저 JS 가 **절대 못 읽음** (HttpOnly). XSS 가 발생해도 토큰
탈취 불가. CSRF 는 SameSite=Lax 로 동일 사이트 요청만 cookie 동반.

## RBAC / Multi-tenant 통합

- `getSession()` (server-only) → `/auth/me` 호출 → `active_org_id` +
  `memberships` + `permissions` 반환
- `(app)/layout.tsx` 의 AuthGuard 가 session 없으면 `/login` redirect
- `OrgSwitcher` — 다중 멤버십이면 select, `POST /auth/switch-org` 로 전환
  (cookie 자동 회전)
- 백엔드는 Day 1-5 (B-0) 에서 cross-tenant 차단 enforce 완료 — 프론트는
  표시만 책임

## 주요 페이지

| 라우트 | 핵심 |
|---|---|
| `/chat` | 검색 + agentic ask + sources expander + meta signals + 오류 신고 |
| `/find-owner` | 담당자 검색, KB 필터, 카드 그리드, 담당 문서 expander |
| `/my-knowledge` | Personal KB CRUD, drag&drop 업로드, soft cap 10 |
| `/my-documents` | 3 탭 (담당 문서 / 대기 작업 / 알림) |
| `/my-feedback` | 2 탭 (피드백 5 type / 오류 신고 7 type × 4 priority) |
| `/search-history` | 페이지네이션 + 클라이언트 필터 + 통계 카드 |

## Admin 화면 (B-2)

운영자용 17 라우트가 별도 route group `(admin)` 에 있습니다. 사용자
화면과 컴포넌트 라이브러리·BFF 인증 흐름은 그대로 공유하되, **shell 만**
차별화 (옵션 A) 했습니다.

### 디자인 차별화

| 축 | 사용자 (`(app)`) | Admin (`(admin)`) |
|---|---|---|
| Accent | indigo `#5e6ad2` | teal `#14b8a6` (dark mode 는 teal-300 `#5eead4`) |
| Sidebar | light/auto theme 따름 | **항상 dark** (slate-800/900) |
| Density | 일반 | 압축 (table dense, 작은 패딩) |
| Brand 라벨 | "axiomedge" | "axiomedge / **Admin**" teal pill |
| Header | 단순 | sticky + breadcrumb + 컨텍스트 액션 + ⌘K 팔레트 + 알림 벨 |

토큰은 `globals.css` 의 `[data-admin="true"]` 와 `[data-admin="true"][data-theme="dark"]`
스코프로 정의 — `<html data-admin="true">` 가 set 된 동안만 활성화.
attribute 는 `AdminHeader` mount effect 가 set/cleanup 하므로 사용자
페이지로 이동하면 자동 해제.

### 17 라우트 매핑

Streamlit admin 21 페이지 → Next.js 17 라우트 (일부 통합).

| 그룹 | 라우트 | 핵심 |
|---|---|---|
| 개요 | `/admin` | 운영 대시보드 — 6 메트릭 카드 + 24h 검색 hero (recharts) |
| 콘텐츠 | `/admin/sources` | 데이터 소스 (Confluence/Slack/etc CRUD + sync trigger) |
| 콘텐츠 | `/admin/ingest` | Ingestion job 큐 + ingestion gate (통합) |
| 콘텐츠 | `/admin/glossary` | 용어집 + components (통합) |
| 콘텐츠 | `/admin/owners` | 담당자 / topic owner 관리 |
| 콘텐츠 | `/admin/groups` | 검색 그룹 정의 |
| 콘텐츠 | `/admin/conflicts` | 중복/모순 chunk pair + resolution |
| 콘텐츠 | `/admin/verification` | 검증 대기 큐 + approve/reject |
| 콘텐츠 | `/admin/lifecycle` | 문서 라이프사이클 (상태 전이) |
| 품질 | `/admin/quality` | RAG 메트릭 (faithfulness/relevancy 등) |
| 품질 | `/admin/golden-set` | golden Q&A CRUD + run evaluation |
| 품질 | `/admin/traces` | Agent trace viewer (`/agentic/traces`) |
| 품질 | `/admin/errors` | 오류 신고 list + resolve |
| 운영 | `/admin/users` | 사용자 / role / 초대 |
| 운영 | `/admin/edge` | Edge model fleet + heartbeat + manifest |
| 운영 | `/admin/jobs` | 백그라운드 job 모니터 |
| 운영 | `/admin/config` | 가중치 / 임계값 view + edit |
| 그래프 | `/admin/graph` | Neo4j entity / relation viewer |

### Auth 가드 (defense in depth)

```
proxy.ts          : 모든 비로그인 요청 → /login (admin 도 동일)
(admin)/layout.tsx: getSession() → hasAdminRole() 체크
                    ├─ session 없음 → /login?next=/admin
                    └─ admin role 없음 → /chat (사용자 화면으로 강제 이동)
```

`hasAdminRole` 은 session.roles 에 `OWNER` 또는 `ADMIN` 이 하나라도
있으면 통과 (`src/components/admin/adminGuard.ts`). 더 세밀한 권한은
페이지/엔드포인트가 추가 검증.

### Admin 전용 컴포넌트 (`src/components/admin/`)

| 컴포넌트 | 역할 |
|---|---|
| `AdminSidebar` | 4 그룹 nav (콘텐츠 / 품질 / 운영 / 그래프) — active 좌측 teal accent bar |
| `AdminHeader` | sticky + breadcrumb + OrgSwitcher + ⌘K 팔레트 + 알림 벨 |
| `AdminQuickPalette` | ⌘K/Ctrl+K 명령 팔레트 — 17 라우트 fuzzy 검색 + 키보드 nav |
| `AdminDashboardClient` | 운영 대시보드 — `useAdminDashboardSummary` + hero + 6 카드 |
| `AreaChartHero` | recharts AreaChart wrapper — 24h 검색 시계열 |
| `Sparkline` | 의존성 없는 SVG line — 카드 우상단 |
| `MetricCard` | label + value + delta + sparkline + tone strip (left accent) |
| `DataTable` | sticky thead + zebra + sticky col + onRowClick — admin list 표준 |
| `SeverityBadge` | severity (info/warning/error/critical) 배지 |
| `*Client.tsx` (17) | 라우트별 client component — `use<Domain>` hook 호출 |

### Admin hook 분류

| 파일 | 책임 도메인 |
|---|---|
| `useAdminDashboard.ts` | 대시보드 summary (`/api/v1/admin/dashboard/summary`) |
| `useDataSources.ts` | 데이터 소스 CRUD + trigger |
| `useContent.ts` | glossary, owners, groups, conflicts, verification, lifecycle |
| `useQuality.ts` | quality, golden-set, traces, errors |
| `useOps.ts` | users, jobs, config |
| `useDistill.ts` | edge model + distill build |
| `useLifecycle.ts` | 문서 라이프사이클 전용 |

### Streamlit deprecate 배너

B-2 시점에 Streamlit admin 19 페이지에 "곧 폐기 — Next.js 로 이동"
배너를 표시 (`src/apps/dashboard/components/deprecate_banner.py`,
`deprecated_for(path, label)`). 배너의 링크 base URL 은
`NEXT_ADMIN_BASE_URL` env 로 override (기본 `http://localhost:3000`).
운영자가 새 환경으로 자연스럽게 이동하도록 유도.

## 배포

### Docker (현재)

```bash
make web-docker-build
docker run --rm -p 3000:3000 \
  -e API_URL=https://api.example.com \
  axiomedge-web:latest
```

### Vercel / 자체 K8s (예정)

B-3 에서 결정. K8s 라면 `src/apps/web/Dockerfile` 그대로 사용 + `deploy/k8s/`
의 manifest 추가.

## Day 1-10 산출물 요약

| Day | 내용 |
|---|---|
| 1 | Personal KB 백엔드 prep (owner_id 필터, auto-create, cap, owner-only 검색 격리) |
| 2 | Next.js 16 부트스트랩 + DESIGN.md (Linear+Notion) + Tailwind v4 토큰 |
| 3 | BFF 인증 (HttpOnly cookie) + AppShell + getSession + OrgSwitcher |
| 4 | API client + 15 endpoint wrapper + 10 UI primitives |
| 5 | /chat (검색/agentic/sources/meta/error report) |
| 6 | /find-owner + /my-feedback (피드백/오류 신고 두 탭) |
| 7 | /my-documents 3 탭 + /search-history |
| 8 | /my-knowledge (Personal KB CRUD + drag&drop 업로드) |
| 9 | i18n (ko/en) + 다크 모드 + skip-to-main + Esc-close + error/loading boundary |
| 10 | Docker 이미지 + 회귀 + 본 문서 |

## 다음 (B-3 ~)

- B-2 ✅ 완료 — admin 17 라우트 + AdminShell + recharts hero + ⌘K 팔레트
  + Streamlit deprecate 배너 (`v0.B-2` tag)
- B-3: 배포 (Vercel 또는 K8s) + i18n en 메시지 완성 + 분석 (Sentry/posthog)
- B-4 (선택): Streamlit admin 완전 폐기 — 모든 운영자 이동 확인 후
  컨테이너 제거
