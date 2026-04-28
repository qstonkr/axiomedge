# User Web UX Redesign — Perplexity-rich + ChatGPT-history

- **Date**: 2026-04-28
- **Owner**: jeongbeom.kim (you.wd@gsretail.com)
- **Scope**: User-facing web (`src/apps/web`, `(app)` route group). Admin/distill/edge are out of scope.
- **Status**: Design approved, pending legal review of retention policy.

## 1. Context & Problem

axiomedge의 사용자 web은 현재 `/chat` 외에 `/find-owner`, `/my-knowledge`, `/my-documents`, `/my-feedback`, `/search-history`, `/my-activities` 등 **7개 페이지가 평면 나열**되어 있다. 이 구조는 다음 문제를 만든다.

1. **메뉴 부담**: 본사 임직원이 매일 쓰는 동선의 90% 이상이 chat인데, chat이 7개 중 하나로 동등하게 표현되어 핵심 가치가 희석된다.
2. **chat history 부재**: 현재 `/chat`은 `sessionStorage` per-tab으로 휘발 저장이라 "어제 물어본 거" 동선을 지원 못 한다. `/search-history`는 별도 페이지로 분리되어 있어 동선이 끊긴다.
3. **출처 검증 미흡**: ChatGPT처럼 답만 보여주는 형태에 가까워, 본사 임직원의 "출처가 어디냐 / 누가 작성했냐 / 신뢰할 만하냐" 동선이 inline에 머물러 무겁다.
4. **모드 결정 부담**: `ModeToggle` (agentic vs. search)이 사용자에게 노출되어 있어, 사용자가 내부 구현 디테일을 의식해야 한다.

## 2. Goals

- **Chat을 단일 메인 surface로 격상.** 좌측 history sidebar + 중앙 chat + 우측 source panel의 3-pane으로 본사 임직원의 깊이·신뢰 검증 동선을 직접 지원한다.
- **Chat history 영구화.** Postgres 저장으로 sessionStorage 휘발 문제를 제거하고, 좌측 sidebar로 노출한다.
- **모드 자동 라우팅.** 사용자가 agentic/search를 직접 고르지 않게 하고, 분류기 신호로 서버에서 라우팅한다.
- **메뉴 7개 → 사실상 1개.** 나머지는 좌측 sidebar / 메시지 액션 / 프로필 드롭다운 / slash 명령으로 흡수.
- **PIPA 준수.** 보존 90일, 사용자 삭제권, at-rest 암호화, 처리방침 갱신.

## 3. Non-Goals

- Admin UI(`(admin)`) 재디자인 — 별건.
- Streamlit dashboard 폐기 — 한동안 병행 운영.
- Distill / Edge 사용자 화면 — 가맹점주/매장 직원 대상이라 별건.
- Slack/Teams 봇, 음성 입력, 본문 전체검색 — 후속.
- 다국어 — 한국어 위주, i18n 키만 채움.

## 4. Target User

- **본사 임직원 (AX 담당자, MD, 운영팀)**. 데스크톱 위주, 깊이 있는 검색, 출처 검증 중요.
- 모바일/태블릿은 보조 동선으로 지원하되 1차 타겟 아님.

## 5. Approach: Perplexity-rich + ChatGPT-history

ChatGPT/Gemini/Perplexity 비교에서 우리 도메인에 가장 맞는 형태:

- **Perplexity**: source 카드를 답변과 동등하게 노출 → 본사 임직원의 출처 검증 요구와 일치.
- **ChatGPT**: 좌측 영구 history → "어제 물어본 거" 동선 핵심.
- **Gemini**: 출처가 약해 그대로는 부적합.

→ "Perplexity인데 대화 history가 ChatGPT처럼 살아있는" 하이브리드.

## 6. Information Architecture

### 6.1 흡수 매핑

| 기존 페이지/기능 | 새 위치 |
|---|---|
| `/search-history` | 좌측 sidebar 자체 (페이지 삭제 + 30일 redirect) |
| `/find-owner` | 메시지 액션 + `/owner` slash 명령 (페이지 삭제 + 30일 redirect) |
| `/my-feedback` | 답변 호버 inline + 프로필 드롭다운 (페이지 축소·메뉴에서 제거) |
| `/my-activities` | 프로필 드롭다운 (페이지 유지·메뉴에서 제거) |
| `/my-documents` | 좌측 sidebar 하단 (페이지 유지) |
| `/my-knowledge` | KB selector "내 KB" 탭 (페이지는 KB 즐겨찾기·기본 설정만) |
| `ModeToggle` 컴포넌트 | 삭제 (자동 라우팅, ⚙️에서 force_mode 토글) |

### 6.2 라우트 구조

```
(app)/
  chat/
    page.tsx                  # 단일 chat surface
    [conversationId]/page.tsx # 특정 대화 진입
  my-documents/page.tsx       # 유지
  my-activities/page.tsx      # 유지 (메뉴에서 빠짐)
  my-knowledge/page.tsx       # 축소 (KB 관리만)
  my-feedback/page.tsx        # 축소 (메뉴에서 빠짐)
  # search-history, find-owner — 삭제 + 30일 redirect to /chat
```

## 7. Layout

### 7.1 Desktop (≥1280px)

```
┌──────────────┬──────────────────────────┬──────────────────┐
│ 좌측 240px   │ 중앙 flex                │ 우측 360px       │
│              │                          │ (collapsible)    │
│ + 새 대화    │  대화 제목 · KB chip · ⚙ │  탭: 출처 / 메타   │
│ ───────      │  ─────────────────────   │  ─────────────   │
│ 검색         │                          │  📎 출처 8건      │
│ 오늘         │  Q: 신촌점 차주 점검?    │   ┌────────────┐ │
│ • 신촌 점검  │  A: …답변[1][2][3]…       │   │정책 v3.2 [1]│ │
│ • PBU 승인   │     [메시지 액션]        │   │ owner: 김… │ │
│ 어제         │                          │   │ KB: g-espa │ │
│ • MD 업무    │  Q: ...                  │   └────────────┘ │
│ ...          │  A: ...                  │   ┌────────────┐ │
│              │                          │   │회의록 [2]   │ │
│ 📂 내 문서   │  ────────────────────    │   └────────────┘ │
│              │  [입력 ⌘+Enter]          │                  │
│ 👤 프로필    │  💡 /owner 김…           │  conf 0.78 · 8s  │
└──────────────┴──────────────────────────┴──────────────────┘
```

### 7.2 좌측 — Conversation List + 보조

- **상단**: `+ 새 대화` 버튼.
- **검색박스**: 대화 제목 incremental 검색 (본문 검색은 v2).
- **목록**: 날짜 그룹 (오늘 / 어제 / 이번 주 / 이전), 호버 시 ✏️rename / 🗑️delete.
- **하단**: 📂 내 문서 (드물게 진입).
- **최하단**: 👤 프로필 드롭다운 → 내 활동 / 내 피드백 / 처리방침 / 로그아웃.

### 7.3 중앙 — Chat

- **상단 thin bar**: 대화 제목 (자동 생성 · 클릭 rename) + KB chip + ⚙️.
- **메시지 스트림**: user / assistant 카드.
  - assistant 카드: 답변 markdown + 인용 마커 `[1] [2]` (호버·클릭 시 우측 source 카드 동기 하이라이트).
  - **메시지 액션** (호버 시 노출): 📎 출처 보기 / 👤 오너 찾기 / 🔁 재질문 / ⚠️ 오답 신고 / 📋 복사.
- **하단 입력**: ChatInput (⌘+Enter), `slash` 자동완성 (`/owner`, `/kb`, `/시간`).

### 7.4 우측 — Source & Context Panel

- **탭 1: 출처** (default): SourceCard 리스트 — 제목 / KB / owner / snippet / 신뢰도 bar. 메시지의 `[N]` 호버 시 동기 하이라이트.
- **탭 2: 메타**: confidence / query_type / search_time_ms / rerank_breakdown / working_memory_hit (현재 `MetaSignals` 컴포넌트 그대로 이전).
- **collapsed 기본**: ≥1280px가 아닌 환경에서는 default closed, 메시지 액션 📎 클릭 시 슬라이드인.

### 7.5 Tablet (768~1280px)

- 우측 panel default closed, 📎 클릭 시 우측 슬라이드인 (overlay).
- 좌측 sidebar 그대로.

### 7.6 Mobile (≤768px)

- 좌측 → drawer (햄버거).
- 우측 → bottom sheet.
- 중앙 chat 풀스크린.

## 8. Data Flow

### 8.1 신규 DB 테이블 (`KnowledgeBase` 메타 디비)

```sql
chat_conversations
├─ id            uuid pk default gen_random_uuid()
├─ user_id       uuid not null
├─ org_id        text not null
├─ title         text not null      -- 자동 요약 (첫 질문 → 짧은 제목)
├─ kb_ids        text[] not null default '{}'
├─ created_at    timestamptz not null default now()
├─ updated_at    timestamptz not null default now()  -- 마지막 메시지 시각
└─ deleted_at    timestamptz                         -- soft delete
index (user_id, deleted_at, updated_at desc)

chat_messages
├─ id              uuid pk default gen_random_uuid()
├─ conversation_id uuid not null fk → chat_conversations(id) on delete cascade
├─ role            text not null check (role in ('user','assistant'))
├─ content_enc     bytea not null     -- pgcrypto 암호화 본문
├─ chunks          jsonb not null default '[]'  -- assistant: [{chunk_id, marker:1, kb_id, doc_title, owner}]
├─ meta            jsonb not null default '{}'  -- confidence, query_type, search_time_ms, rerank_breakdown, ...
├─ trace_id        text
└─ created_at      timestamptz not null default now()
index (conversation_id, created_at)
```

`pgcrypto` extension 활성화 필요. content는 `pgp_sym_encrypt(text, key)`로 저장, 키는 KMS 또는 settings에서 주입.

### 8.2 API

| 메서드 | 경로 | 역할 |
|---|---|---|
| GET    | `/api/v1/chat/conversations` | 좌측 sidebar — paged, 날짜 그룹 |
| POST   | `/api/v1/chat/conversations` | 새 대화 생성 (첫 메시지 보낼 때 자동) |
| PATCH  | `/api/v1/chat/conversations/{id}` | rename |
| DELETE | `/api/v1/chat/conversations/{id}` | soft delete (즉시), hard delete은 다음 cron |
| GET    | `/api/v1/chat/conversations/{id}/messages` | 대화 열람 |
| POST   | `/api/v1/chat/conversations/{id}/messages` | 메시지 전송 — 라우팅 + 저장 wrapper |

기존 `/api/v1/search` 와 `/api/v1/agentic/ask` 는 **그대로 유지**. 새 wrapper는 위 둘을 호출하고 결과를 DB에 저장만 추가.

### 8.3 자동 모드 라우팅 (서버 wrapper 내부)

```python
# src/api/routes/chat.py 신규
async def route_query(query: str, force_mode: str | None) -> Literal["search","agentic"]:
    if force_mode in ("quick","deep"):
        return "search" if force_mode == "quick" else "agentic"
    signals = await query_classifier.analyze(query)  # 기존 component
    if signals.intent_count > 1: return "agentic"
    if signals.requires_followup: return "agentic"
    if signals.ambiguity_score > 0.6: return "agentic"
    return "search"
```

force_mode는 ⚙️에서 사용자가 강제 가능 (`auto` / `quick` / `deep`).

### 8.4 프론트 state

- 제거: 기존 `useChatStore`의 sessionStorage 동기화.
- 신규:
  - `useConversationsQuery()` — TanStack Query, 좌측 sidebar 데이터.
  - `useConversationMessagesQuery(id)` — 특정 대화의 메시지.
  - `useChatStore` — 현재 열린 대화의 turns만, 서버 응답으로 동기화. optimistic update로 user turn 즉시 노출.
- KbSelector / SourcePanel은 zustand 또는 query 캐시 어디서든 OK.

### 8.5 인용 마커 ↔ source 카드 동기

- assistant 메시지 본문에 `[1]`, `[2]` 마커는 LLM 응답 시 prompt로 강제하거나 후처리에서 chunks 순서대로 매핑.
- 마커 hover/click → 우측 패널의 해당 source 카드 highlight + scroll into view (CSS class toggle).

## 9. Migration

### 9.1 페이지 마이그레이션

| 페이지 | 처리 |
|---|---|
| `/chat` | 3-pane 재구성 |
| `/search-history` | **삭제** + 30일 redirect → `/chat` |
| `/find-owner` | **삭제** + 30일 redirect → `/chat` (welcome tooltip로 `/owner` 안내) |
| `/my-feedback` | 축소 (메뉴에서 제거, 프로필 드롭다운에서 진입) |
| `/my-activities` | 메뉴에서 제거, 프로필 드롭다운에서 진입 |
| `/my-documents` | 좌측 sidebar 하단에서 진입 |
| `/my-knowledge` | KB 즐겨찾기·기본 설정 화면으로 축소 |
| `/login`, `/admin/*` | 변경 없음 |

### 9.2 DB 마이그레이션

- 신규 테이블 2개 추가 — 다운타임 0.
- `pgcrypto` extension 활성화 (`CREATE EXTENSION IF NOT EXISTS pgcrypto;`).
- 기존 sessionStorage 데이터는 이전하지 않음 (휘발성).

### 9.3 출시 단계

1. PR 1: DB 마이그레이션 + 신규 wrapper API + 라우팅 분류기 (백엔드, 기능 플래그 default off).
2. PR 2: ConversationSidebar / SourcePanel 컴포넌트 + 신규 chat store (프론트, 기능 플래그 default off).
3. PR 3: ChatPage 3-pane 재구성, 메시지 액션 / slash 명령.
4. PR 4: 페이지 삭제·redirect, 프로필 드롭다운, 메뉴 정리.
5. PR 5: 처리방침 / 동의 모달 / 보존 cron / 테스트 / 법무 review 후 기능 플래그 ON.

## 10. Testing

| 레이어 | 방법 |
|---|---|
| Backend unit | `tests/unit/test_chat_routes.py` — wrapper 라우팅 분기, 보존 cron, 암호화 round-trip |
| Frontend unit | Vitest — ConversationSidebar / MessageActions / SourcePanel, store mock |
| Integration | 신규 wrapper → `/search` 또는 `/agentic/ask` 분기 → DB 저장 (real Postgres 컨테이너) |
| E2E | Playwright — 새 대화 → 메시지 → sidebar 갱신 → 출처 클릭 → 우측 패널 하이라이트 → rename → delete |
| Regression | 기존 `/search`, `/agentic/ask` 직접 호출 시 동작 변하지 않음 (기존 5,000+ 테스트 그대로) |
| Security | DELETE 권한(본인 대화만), 90일 cron, 암호화 컬럼 직접 SELECT 시 plaintext 미노출 |

## 11. Risks & Mitigation

| 리스크 | 영향 | 대응 |
|---|---|---|
| 자동 모드 라우팅 오분류 | "deep 질문에 quick 답" → 답 부실 | (a) ⚙️ "deep으로 다시" 버튼, (b) confidence 낮으면 자동 escalate, (c) routing 결정을 audit_log에 기록해 사후 분석 |
| pgcrypto → 본문 검색 못 함 | sidebar 본문 검색 불가 | sidebar 검색은 **제목 only**. 본문 검색은 v2 (검색 인덱스 별도) |
| sessionStorage → DB 전환 latency | 첫 진입 200~500ms | TanStack Query prefetch + skeleton |
| 법무 review 지연 | 출시 블로킹 | 구현 병행, 처리방침/동의 모달이 마지막 step. 기능 플래그로 리뷰 전 후 모두 dark launch 가능 |
| `/find-owner` 사용자 학습 비용 | 슬래시 명령 모름 | 30일 redirect 페이지에 "이제 `/owner 이름`으로도 가능" 안내 + 입력 placeholder + 1회 onboarding tooltip |
| 패널 너무 차지 | 1280px 미만에서 답답 | 우측 default collapsed, 좌측은 768px 이하 drawer |

## 12. Legal & Compliance

PIPA(개인정보보호법) 기준 권고 사항. 사내 법무·정보보안팀 review 필요.

| 항목 | 처리 |
|---|---|
| 보존 기간 | **90일** 자동 파기 (`chat_history_purge` arq job, 야간) |
| 사용자 삭제권 (PIPA §36) | 좌측 sidebar에서 대화 단위 삭제. soft → 다음 cron에서 hard delete. 백업은 백업 정책 사이클대로 파기 |
| 본문 암호화 (at-rest) | pgcrypto `pgp_sym_encrypt`, 키는 settings/KMS |
| 처리방침 | `docs/SECURITY.md` 업데이트 + 첫 로그인 1회 동의 모달. 문구: "AI 검색 질의·답변은 시스템 개선·감사 목적으로 90일 보관 후 자동 파기" |
| 목적 외 이용 금지 | RBAC — 본인 대화만 조회·삭제. 감사 접근은 별도 role |
| 민감정보 | content_enc 암호화로 갈음 |
| DPIA | 기존 시스템 DPIA에 부록 추가 (회사 정책에 따라) |

**Review items (open)**:
- 사내에 도입된 다른 사내 AI(뤼튼/ChatGPT Team/사내 LLM 등)의 처리방침 선례가 있는지 확인 → 있으면 그대로 차용해 review 가속.
- 보존 90일이 사내 정책과 일관 되는지 확인 (회의록 저장 정책 등).

## 13. Out-of-Scope (명시)

- Admin UI 재디자인.
- Streamlit dashboard 폐기.
- Distill / Edge 사용자 화면.
- Slack / Teams 봇 통합.
- 음성 입력.
- 본문 전체 검색(v2).
- 다국어 i18n 본격화.

## 14. Effort Estimate

5–7 PR (대형 단일 PR 안 함):

- DB 마이그레이션 + wrapper API + 라우팅 — 1~2 PR
- 좌측 sidebar + 우측 panel + 메시지 액션 — 2~3 PR
- 페이지 삭제·redirect + 프로필 드롭다운 — 1 PR
- 테스트·E2E·문서·법무 자료 — 1 PR
