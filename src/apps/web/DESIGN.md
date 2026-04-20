# axiomedge DESIGN.md

axiomedge web 의 디자인 시스템 SSOT. **Linear** (productivity SaaS 의
intentional minimalism) + **Notion** (content-first, soft elevation) 두 톤을
합성. 모든 페이지/컴포넌트는 이 토큰만 사용하고 ad-hoc 색·여백·라운딩 값을
하드코딩하지 않는다.

토큰의 실제 값은 `src/app/globals.css` 의 `:root` / `[data-theme="dark"]`
블록에서 정의되며, Tailwind v4 의 `@theme inline` 으로 바로 utility 가 됨
(예: `bg-bg-canvas`, `text-fg-muted`, `rounded-md`, `shadow-sm`).

---

## Tone & Voice

- **차분하고 의도적**: 채도를 낮춘 indigo accent, 큰 chrome 없음
- **Content-first**: 컨텐츠 (검색 결과, 문서, 답변) 가 시각적 무게의 주체
- **밀도 조절**: 검색/대화 화면은 dense, 폼/설정은 generous
- **한국어 우선**: 폰트 stack 의 head 에 Pretendard, fallback 에 Apple SD
  Gothic Neo / Noto Sans KR
- **모션 절제**: hover/focus 만 transition. 200ms ease-out 기본

---

## Color Tokens

### Neutral (background / foreground / border)

| Token | Light | Dark | 용도 |
|---|---|---|---|
| `bg-canvas` | `#ffffff` | `#1a1a1a` | 페이지 기본 배경 |
| `bg-subtle` | `#f7f7f5` | `#232323` | 섹션 배경, sidebar |
| `bg-muted` | `#efeeec` | `#2c2c2c` | hover, selected |
| `bg-emphasis` | `#e3e2e0` | `#383838` | active, pill 배경 |
| `fg-default` | `#1f1f1f` | `#ededed` | body 텍스트 |
| `fg-muted` | `#5f5e5b` | `#b6b3ac` | 보조 텍스트, 메타 |
| `fg-subtle` | `#8a8a87` | `#8a8a87` | placeholder, hint |
| `fg-onAccent` | `#ffffff` | `#ffffff` | accent 위 텍스트 |
| `border-default` | `#e3e2e0` | `#323232` | 카드/입력 1px |
| `border-strong` | `#c6c5c2` | `#4a4a4a` | 강조 경계 |

### Accent (Linear-style indigo)

| Token | Light | Dark | 용도 |
|---|---|---|---|
| `accent-default` | `#5e6ad2` | `#8389e3` | primary 버튼, link |
| `accent-emphasis` | `#4d57b5` | `#6770d4` | hover, active |
| `accent-subtle` | `#eef0fb` | `#2c2e54` | badge/tag 배경 |

### Semantic

| Token | Light | Dark | 용도 |
|---|---|---|---|
| `success-default` | `#2f9461` | (same) | toast 성공, 정상 상태 |
| `success-subtle` | `#eaf6ef` | `#1f3a2c` | 성공 배경 |
| `danger-default` | `#d23f3f` | (same) | 삭제, 에러 |
| `danger-subtle` | `#fbecec` | `#43221f` | 에러 배경 |
| `warning-default` | `#c47a16` | (same) | stale, caution |
| `warning-subtle` | `#fbf2e2` | `#3a2e15` | warning 배경 |

---

## Spacing

Tailwind 기본 spacing scale (`0/0.5/1/1.5/2/3/4/5/6/8/10/12/16/20/24`) 그대로
사용. 임의 px 금지. 아이템 사이 간격 가이드:

| 맥락 | 권장 |
|---|---|
| 인라인 텍스트 사이 | `gap-2` (8px) |
| 입력 폼의 행 사이 | `gap-4` (16px) |
| 카드 내부 padding | `p-4` ~ `p-6` |
| 페이지 좌우 padding | `px-6` (mobile) ~ `px-12` (desktop) |
| 섹션 간격 | `space-y-8` ~ `space-y-12` |

---

## Radius

| Token | Px | 용도 |
|---|---|---|
| `rounded-sm` | 4 | tag, 작은 칩 |
| `rounded-md` | 6 | input, button, card |
| `rounded-lg` | 10 | 큰 카드, 모달 |
| `rounded-xl` | 16 | 영웅 영역, dropdown panel |
| `rounded-pill` | 9999 | 상태 badge |

---

## Shadows (Notion soft elevation)

| Token | 용도 |
|---|---|
| `shadow-xs` | 미세 강조 (input focus 등) |
| `shadow-sm` | card resting |
| `shadow-md` | dropdown, popover |
| `shadow-lg` | modal, dialog |

---

## Typography

```
Display    text-3xl    font-semibold   leading-tight  → 페이지 헤더
Heading    text-2xl    font-semibold   leading-snug   → 섹션 헤더
Title      text-lg     font-medium     leading-snug   → 카드 제목
Body       text-sm     font-normal     leading-6      → 본문 (기본)
Caption    text-xs     font-normal     leading-5      → 메타, 시각 보조
Mono       text-xs     font-mono                       → kb_id, 코드
```

한국어 본문 줄간격은 `leading-6` (24px on text-sm) 기본 — 줄 사이가 좁으면
가독성 급락. 수치 정렬은 `tabular-nums` utility 적용.

---

## Component Specs

### Button

```
size: sm (h-8 px-3) | md (h-9 px-4 — default) | lg (h-10 px-5)
variant:
  primary    : bg-accent-default → emphasis on hover, fg-onAccent
  secondary  : bg-bg-emphasis    → muted on hover, fg-default
  ghost      : transparent       → bg-bg-muted on hover
  danger     : bg-danger-default → 어둡게 on hover
state: default | hover | focus-visible (accent ring) | disabled (opacity-50)
icon: 좌/우 16px 단일 가능
```

### Card

```
배경: bg-bg-canvas
경계: border border-border-default rounded-lg
elevation: shadow-sm (resting) → shadow-md (hover, optional)
padding: p-6 (default), p-4 (compact)
heading: Title 스케일, fg-default
body: Body 스케일, fg-muted
```

### Input / Textarea

```
높이: h-9 (input), min-h-[112px] (textarea)
경계: border-border-default → accent-default focus
배경: bg-bg-canvas
font: text-sm font-normal
placeholder: fg-subtle
disabled: bg-bg-subtle text-fg-muted cursor-not-allowed
error: border-danger-default + caption text-danger-default 아래 표시
```

### Tabs (Notion-style underline)

```
컨테이너: border-b border-border-default
아이템: px-4 py-2 text-sm fg-muted
active: fg-default + 2px underline (accent-default)
hover: bg-bg-muted (subtle)
keyboard: ←/→ 로 이동
```

### Sidebar

```
폭: w-64 (collapsed: w-14, icon-only)
배경: bg-bg-subtle
구분선: border-r border-border-default
아이템:
  default  : px-3 py-2 rounded-md fg-muted hover:bg-bg-muted
  active   : bg-bg-emphasis fg-default font-medium
  with icon: 좌측 18px 아이콘 + 12px gap
groups: 회색 caption 헤더 (text-xs uppercase fg-subtle px-3 mt-4 mb-1)
```

### Toast

```
위치: fixed bottom-6 right-6 (stack 위로)
폭: w-80
배경: bg-bg-canvas, border border-border-default, shadow-md, rounded-lg
타입별 좌측 4px 색상 strip:
  success → success-default
  danger  → danger-default
  warning → warning-default
  info    → accent-default
```

### Dialog / Modal

```
overlay: bg-black/40 (light) | bg-black/60 (dark) backdrop-blur-sm
panel: bg-bg-canvas border border-border-default rounded-lg shadow-lg p-6
폭: max-w-md (default), max-w-2xl (form-heavy)
헤더: Heading + 닫기 버튼 (ghost icon)
액션: 우측 정렬 (cancel ghost, primary)
```

### Source Card (검색 결과)

```
컨테이너: Card (compact, p-4)
헤더 한 줄:
  - Title (KB 이름 또는 문서명)
  - tier badge (global/team/personal — accent-subtle, success-subtle, ...)
  - 신뢰도 score (Caption mono, fg-muted)
본문:
  - 인용된 chunk text (text-sm, line-clamp-3)
  - 출처 URI (Caption fg-subtle, hover로 underline)
액션: "투명성 보기" / "오류 신고" ghost 버튼
```

---

## Patterns

### Empty State

```
중앙 정렬, 200px 빈 영역
아이콘 (96x96 fg-subtle)
Heading + Body fg-muted
선택적 primary 버튼 (e.g. "새 KB 만들기")
```

### Loading

- 페이지 전체: `<Skeleton />` (placeholder shapes), 절대 spinner 풀스크린 X
- 인라인 (버튼 등): 좌측 16px spinner + 비활성화

### Error

- 인라인: `Toast` (3초 자동 dismiss) + form field 의 caption-level 메시지
- 치명적: `error.tsx` 풀페이지 — Heading + Body + "다시 시도" primary 버튼

### Density

- 데이터 테이블 (검색 이력 등): `text-xs` row, padding `py-2 px-3`
- 폼: row 사이 `gap-4`, label `mb-1` Caption fg-muted

---

## Admin Shell (B-2)

사용자 화면 (`(app)`) 과 admin 화면 (`(admin)`) 은 **같은 컴포넌트 라이브러리** 를
사용하되, **shell (sidebar/header/accent/density) 만 차별화** 한다. 사용자가
URL 만 보고 있는 영역을 식별할 필요 없이, 페이지 진입 즉시 시각적으로 구분된다.

차별화는 `<html data-admin="true">` 스코프로 토큰 override 하는 방식 — 별도
컴포넌트 fork 없음. AdminShell layout 이 `document.documentElement.dataset.admin`
을 set 하고 unmount 시 unset.

### Tone

- **운영자 톤** — Sentry/Posthog/Datadog 패턴 합성
- **dark sidebar + light content** (운영 dashboard 의 보편 패턴)
- **압축된 density** — table dense, compact rows, sticky header
- **teal accent** — 사용자 화면 indigo (`#5e6ad2`) 와 hue 충분히 분리

### Color Tokens (admin override)

`[data-admin="true"]` 스코프에서만 적용. 나머지는 `:root` 토큰 그대로 사용.

| Token | Light (admin) | Dark (admin) | 용도 |
|---|---|---|---|
| `accent-default` | `#14b8a6` (teal-500) | `#2dd4bf` (teal-400) | admin primary |
| `accent-emphasis` | `#0d9488` (teal-600) | `#14b8a6` (teal-500) | admin hover |
| `accent-subtle` | `#ccfbf1` (teal-100) | `#134e4a` (teal-900) | admin badge bg |
| `admin-sidebar-bg` | `#1f2937` (slate-800) | `#0f172a` (slate-900) | sidebar 항상 dark |
| `admin-sidebar-fg` | `#cbd5e1` (slate-300) | `#cbd5e1` | sidebar 텍스트 |
| `admin-sidebar-active-bg` | `#0f766e` (teal-700) | `#0d9488` | sidebar 선택 항목 bg |
| `admin-sidebar-hover-bg` | `#374151` (slate-700) | `#1e293b` (slate-800) | sidebar hover |

### Severity Tokens (운영 alert)

| Token | Hex (light) | 용도 |
|---|---|---|
| `severity-info` | `#3b82f6` | INFO log |
| `severity-warn` | `#c47a16` | WARN log, queued job |
| `severity-error` | `#d23f3f` | ERROR log, failed job |
| `severity-critical` | `#7f1d1d` | CRITICAL alert, P0 incident |
| `severity-success` | `#2f9461` | success state, healthy |

### Density Tokens

| 컨텍스트 | spacing | typography |
|---|---|---|
| admin table row | `py-1.5 px-2` | `text-xs leading-snug` |
| admin form row | `gap-3` | `text-sm` |
| admin section gap | `gap-4` | — |
| admin metric card | `p-4` | `text-3xl` value + `text-xs` label |

### Shell Layout

```
<html data-admin="true">
  <body>
    <div class="flex min-h-screen">
      <AdminSidebar />              ← w-60, dark bg, teal active
      <div class="flex-1 flex flex-col">
        <AdminHeader />             ← sticky top-0, h-12, breadcrumb + actions
        <main class="overflow-auto p-6">{children}</main>
      </div>
    </div>
  </body>
</html>
```

### Brand 라벨

좌상단 sidebar header 또는 main header 좌측에 항상 노출:

```
axiomedge ▸ [Admin]   ← teal pill, text-xs uppercase
```

### Components (admin 전용)

| 컴포넌트 | 출처 패턴 | 용도 |
|---|---|---|
| `<MetricCard label value delta sparkline />` | Posthog dashboard | KPI 표시 |
| `<Sparkline points />` | SVG only, no deps | metric card 보조 |
| `<DataTable columns rows />` | Linear/Stripe 합성 | KB/문서/owner list |
| `<LogViewer events />` | Sentry event log | job/edge log tail |
| `<EventTimeline events />` | Sentry breadcrumb | ingest pipeline 단계 |
| `<SeverityBadge level />` | Sentry severity color | alert/log row |
| `<AdminSidebar groups />` | dark sidebar with sections | 4 그룹 메뉴 |
| `<AdminHeader breadcrumb actions />` | sticky compact | 페이지 컨텍스트 |
| `<KbScopePicker />` | 재사용 가능 | 다수 admin 페이지에서 사용 |

### Reference patterns

- **Posthog**: 메트릭 카드 그리드 (4×N), filter chips (좌측 좁은 column),
  시계열 차트 with hover tooltip
- **Sentry**: severity color (info/warn/error/critical) 일관 적용,
  event timeline (수직 line + 단계별 마커), stack-trace 같은 dense detail
  panel, breadcrumb context
- **Supabase**: table editor (inline edit, row hover action, column resize),
  schema visual

---

## Accessibility — WCAG 2.1 contrast

`globals.css` 의 모든 색상 pair 는 WCAG 2.1 contrast ratio 를 만족하도록
조정. 자동 audit 결과 (2026-04-20):

**모든 텍스트/UI 색상 AA 통과** — admin teal accent 는 처음 #14b8a6 (teal-500,
2.49:1 FAIL) 이었으나 #0d9488 (teal-600, 3.74:1 ✅) 로 한 단계 어둡게 조정.
`fg-subtle` 도 #8a8a87 (3.46:1 large only) → #6e6c69 (4.79:1 normal text 통과)
로 보정.

**의도적 예외 (audit FAIL 이지만 유지):**
- `border-default` (#e3e2e0) / `border-strong` (#c6c5c2) on white
  - 1.29 / 1.73 — WCAG 1.4.11 "Non-text Contrast" 는 정보 식별에 essential
    한 UI 에만 3:1 요구. card box 의 visual divider 는 essential 이 아님
    (정보는 fg/bg contrast 로 식별 가능).
  - Notion/Linear 도 같은 정책 — neutral separator 는 약한 contrast 가 디자인 의도.
- `fg-onAccent (white) on accent` dark theme & admin teal-600 — 3.16 / 3.74
  - 큰 텍스트 (18pt+ 또는 14pt bold) 에서만 사용 권장. Button 의 default
    size 가 작은 14px 인 경우 시각 검토 필요. admin sidebar active item 도
    동일 — 시각 검토에 의존.

audit 스크립트: `/tmp/contrast_audit.py` (개발자 환경 — 새 토큰 추가 시
재실행 권장).

---

## Reference

- Linear DESIGN.md: `/Users/jeongbeom.kim/axiomedge/sandbox/awesome-design-md/design-md/linear.app/README.md`
- Notion DESIGN.md: `/Users/jeongbeom.kim/axiomedge/sandbox/awesome-design-md/design-md/notion/README.md`
- Posthog DESIGN.md: `/Users/jeongbeom.kim/axiomedge/sandbox/awesome-design-md/design-md/posthog/README.md`
- Sentry DESIGN.md: `/Users/jeongbeom.kim/axiomedge/sandbox/awesome-design-md/design-md/sentry/README.md`
- Supabase DESIGN.md: `/Users/jeongbeom.kim/axiomedge/sandbox/awesome-design-md/design-md/supabase/README.md`
- 토큰 정의: `src/app/globals.css`
- Tailwind v4 가이드: `node_modules/tailwindcss/dist/...` (CSS-first config)
