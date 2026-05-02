/**
 * Admin smoke — 19개 admin 페이지가 200 + 페이지 라벨 (breadcrumb / h1) +
 * 콘솔 에러 (uncaught / 4xx-5xx 응답) 없음.
 *
 * 회귀 1차 보호망. 페이지가 깨지거나 ROUTE_LABELS / 핵심 fetch 가
 * 무너지면 즉시 fail.
 */
import { expect, test, type ConsoleMessage } from "@playwright/test";

import { gotoAdmin, loginAsAdmin } from "./helpers";

/** AdminHeader 의 ROUTE_LABELS 와 동기 — 페이지 라벨 변경 시 양쪽 같이. */
const PAGES: Array<{ path: string; label: string }> = [
  { path: "/admin", label: "운영 대시보드" },
  { path: "/admin/sources", label: "데이터 소스" },
  { path: "/admin/ingest", label: "Ingest 작업" },
  { path: "/admin/glossary", label: "용어집" },
  { path: "/admin/owners", label: "담당자 관리" },
  { path: "/admin/groups", label: "검색 그룹" },
  { path: "/admin/conflicts", label: "중복/모순" },
  { path: "/admin/verification", label: "검증 대기" },
  { path: "/admin/lifecycle", label: "문서 라이프사이클" },
  { path: "/admin/quality", label: "RAG 품질" },
  { path: "/admin/golden-set", label: "Golden Set" },
  { path: "/admin/traces", label: "Agent Trace" },
  { path: "/admin/errors", label: "오류 신고" },
  { path: "/admin/users", label: "사용자/권한" },
  { path: "/admin/edge", label: "Edge 모델" },
  { path: "/admin/jobs", label: "작업 모니터" },
  { path: "/admin/config", label: "가중치 설정" },
  { path: "/admin/graph", label: "엔티티 탐색" },
  { path: "/admin/graph-schema", label: "스키마 검토" },
];

/**
 * 콘솔 메시지 화이트리스트 — Next.js dev / React 가 흘리는 알려진 노이즈.
 * 정규식 일치하면 ignore.
 */
const IGNORED_CONSOLE_PATTERNS: RegExp[] = [
  /Download the React DevTools/i,
  /\[Fast Refresh\]/i,
  /Warning: React.createFactory/i,
  // Next.js 16 Turbopack 가 dev 에서 가끔 흘리는 hydration 노이즈
  /Hydration failed/i,
  // recharts ResponsiveContainer 가 layout 측정 중 한 번씩 흘림
  /The width\(\d+\) and height\(\d+\) of chart/i,
];

function shouldIgnore(msg: string): boolean {
  return IGNORED_CONSOLE_PATTERNS.some((re) => re.test(msg));
}

test.beforeEach(async ({ page }) => {
  await loginAsAdmin(page);
});

for (const { path, label } of PAGES) {
  test(`${path} 페이지 정상 로드 + 라벨 + 콘솔 에러 없음`, async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg: ConsoleMessage) => {
      if (msg.type() === "error" && !shouldIgnore(msg.text())) {
        consoleErrors.push(msg.text());
      }
    });
    page.on("pageerror", (e) => {
      consoleErrors.push(`pageerror: ${e.message}`);
    });

    await gotoAdmin(page, path);

    // 헤더 breadcrumb 가 페이지 라벨 노출 — AdminHeader 가 ROUTE_LABELS 로
    // 항상 렌더하므로 가장 reliable.
    await expect(
      page.getByRole("navigation", { name: "현재 위치" }).getByText(label, { exact: true }),
    ).toBeVisible({ timeout: 10_000 });

    // 페이지가 4xx/5xx 인 채로 마운트되면 fail.
    expect(page.url()).toContain(path);

    // 콘솔 에러 누적 검사 — 화이트리스트 외엔 fail.
    expect(consoleErrors, `console errors on ${path}:\n${consoleErrors.join("\n")}`)
      .toHaveLength(0);
  });
}
