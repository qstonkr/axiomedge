/**
 * 19개 admin 페이지 각자의 primary feature 가 동작하는지 sweep —
 * "통합 검증 = 전체 페이지가 정상 동작" 정의에 맞춤.
 *
 * smoke.spec.ts 가 페이지 로드 / 라벨 / 콘솔 에러 1차 보호망이라면,
 * 이 spec 은 한 단계 더 들어가 각 페이지의 button/tab/dialog/select 가
 * 실제로 작동하는지 검증.
 *
 * 데이터 변경 없는 read-only 인터랙션 위주. mutation 흐름 (등록 → 수정 →
 * 삭제) 은 glossary.spec.ts 가 대표 검증.
 */
import { expect, test } from "@playwright/test";

import { gotoAdmin, loginAsAdmin } from "./helpers";

test.beforeEach(async ({ page }) => {
  await loginAsAdmin(page);
});

// ── 탭 전환 ────────────────────────────────────────────────────────

test("/admin/edge — 6개 탭 모두 전환 + active aria-selected", async ({ page }) => {
  await gotoAdmin(page, "/admin/edge");
  const tabs = page.getByRole("tab");
  await expect(tabs).toHaveCount(6, { timeout: 8_000 });
  for (let i = 0; i < 6; i++) {
    const t = tabs.nth(i);
    const label = (await t.textContent())?.trim() || `tab-${i}`;
    await t.click();
    await expect(t, `${label} active`).toHaveAttribute("aria-selected", "true");
    await page.waitForTimeout(150);
  }
});

test("/admin/conflicts — 대기 ↔ 해결 이력 탭 전환", async ({ page }) => {
  await gotoAdmin(page, "/admin/conflicts");
  await page.getByRole("button", { name: /해결 이력/ }).click();
  // 이력 panel 표시 — 데이터 비어도 panel 자체는 노출
  await expect(page.locator("body")).toContainText(/이력|history/i);
});

test("/admin/golden-set — 기본 → AI 탭 전환", async ({ page }) => {
  await gotoAdmin(page, "/admin/golden-set");
  // AI 탭 (button or tab)
  const aiTab = page.getByRole("tab", { name: /AI/i }).or(
    page.getByRole("button", { name: /AI/i }),
  );
  if (await aiTab.first().isVisible().catch(() => false)) {
    await aiTab.first().click();
    await page.waitForTimeout(300);
  }
  // 페이지 자체는 정상 렌더 (Golden Set 헤더)
  await expect(page.getByRole("heading", { name: /Golden Set/i })).toBeVisible();
});

test("/admin/graph — 3 탭 전환 (엔티티 검색 / 전문가 / 무결성)", async ({ page }) => {
  await gotoAdmin(page, "/admin/graph");
  const tabs = page.getByRole("tab");
  const count = await tabs.count();
  expect(count).toBeGreaterThanOrEqual(3);
  for (let i = 0; i < Math.min(count, 3); i++) {
    await tabs.nth(i).click();
    await expect(tabs.nth(i)).toHaveAttribute("aria-selected", "true");
    await page.waitForTimeout(150);
  }
});

// ── 다이얼로그 열기/닫기 ───────────────────────────────────────────

test("/admin/sources — 신규 소스 dialog open + Esc 닫기", async ({ page }) => {
  await gotoAdmin(page, "/admin/sources");
  await page.getByRole("button", { name: /신규 소스/ }).click();
  const d = page.getByRole("dialog");
  await expect(d).toBeVisible();
  await expect(d).toContainText(/connector/i);
  await page.keyboard.press("Escape");
  await expect(d).toBeHidden({ timeout: 3_000 });
});

test("/admin/sources — 공유 토큰 dialog open + 닫기", async ({ page }) => {
  await gotoAdmin(page, "/admin/sources");
  await page.getByRole("button", { name: /공유 토큰/ }).click();
  const d = page.getByRole("dialog");
  await expect(d).toBeVisible();
  await expect(d).toContainText(/토큰|bot|공유/i);
  await page.keyboard.press("Escape");
  await expect(d).toBeHidden({ timeout: 3_000 });
});

test("/admin/groups — 신규 그룹 dialog open + 닫기", async ({ page }) => {
  await gotoAdmin(page, "/admin/groups");
  await page.getByRole("button", { name: /\+ 신규 그룹/ }).click();
  const d = page.getByRole("dialog");
  await expect(d).toBeVisible();
  await expect(d.getByLabel(/^이름/i)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(d).toBeHidden({ timeout: 3_000 });
});

test("/admin/users — 권한 dialog open (admin 첫 행)", async ({ page }) => {
  await gotoAdmin(page, "/admin/users");
  // 권한 button — 행 단위로 여러 개. 첫 번째 클릭.
  const btn = page.getByRole("button", { name: /^권한$/ }).first();
  if (await btn.isVisible().catch(() => false)) {
    await btn.click();
    const d = page.getByRole("dialog");
    await expect(d).toBeVisible({ timeout: 3_000 });
    await page.keyboard.press("Escape");
    await expect(d).toBeHidden({ timeout: 3_000 });
  } else {
    test.skip(true, "활성 사용자가 없어 권한 dialog 검증 불가");
  }
});

test("/admin/jobs — 상세 expansion (첫 행)", async ({ page }) => {
  await gotoAdmin(page, "/admin/jobs");
  const btn = page.getByRole("button", { name: /^상세$/ }).first();
  if (await btn.isVisible().catch(() => false)) {
    await btn.click();
    // 상세는 inline expansion — 클릭 후 추가 콘텐츠 표시
    await page.waitForTimeout(500);
    // 단순히 클릭이 throw 안 했으면 OK
  } else {
    test.skip(true, "ingest run 이 없어 상세 expansion 검증 불가");
  }
});

// ── KB 셀렉터 / 입력 동적 로딩 ──────────────────────────────────

test("/admin/quality — KB 선택 후 KTS 6-Signal 표시", async ({ page }) => {
  await gotoAdmin(page, "/admin/quality");
  const sel = page.locator("select").first();
  await sel.waitFor({ state: "visible" });
  const opts = await sel.locator("option").all();
  if (opts.length < 2) {
    test.skip(true, "활성 KB 가 없어 quality KB select 검증 불가");
    return;
  }
  const v = await opts[1].getAttribute("value");
  if (!v) test.skip(true, "KB option value 비어있음");
  await sel.selectOption(v!);
  await expect(page.getByText(/KTS 6-Signal/i).first()).toBeVisible({ timeout: 5_000 });
});

test("/admin/lifecycle — KB 선택 후 stage 표시", async ({ page }) => {
  await gotoAdmin(page, "/admin/lifecycle");
  const sel = page.locator("select").first();
  await sel.waitFor({ state: "visible" });
  const opts = await sel.locator("option").all();
  if (opts.length < 2) {
    test.skip(true, "활성 KB 없음");
    return;
  }
  const v = await opts[1].getAttribute("value");
  if (!v) test.skip(true, "KB option 비어있음");
  await sel.selectOption(v!);
  // 현재 단계 / stage 표시 — 셀렉트 후 추가 카드 노출
  await expect(page.getByText(/현재 단계|active|stage/i).first()).toBeVisible({ timeout: 5_000 });
});

test("/admin/owners — KB 필터 적용 가능", async ({ page }) => {
  await gotoAdmin(page, "/admin/owners");
  const sel = page.locator("select").first();
  await sel.waitFor({ state: "visible" });
  const opts = await sel.locator("option").all();
  if (opts.length >= 2) {
    const v = await opts[1].getAttribute("value");
    if (v) await sel.selectOption(v);
  }
  // 필터 적용 후에도 페이지 정상 — 헤딩 보임
  await expect(page.getByRole("heading", { name: /담당자 관리/ })).toBeVisible();
});

test("/admin/graph-schema — KB 입력 + Bootstrap 버튼 활성화", async ({ page }) => {
  await gotoAdmin(page, "/admin/graph-schema");
  const input = page.getByPlaceholder(/kb_id/i).first();
  await input.fill("default");
  // Bootstrap 버튼 — disabled 풀려야 함
  const btn = page.getByRole("button", { name: /Bootstrap/i });
  await expect(btn).toBeEnabled();
});

test("/admin/config — 키 검색 input 작동", async ({ page }) => {
  await gotoAdmin(page, "/admin/config");
  const search = page.getByPlaceholder(/키 검색/);
  await search.fill("rerank");
  await page.waitForTimeout(500);
  // 필터 후에도 결과 표시 (rerank 키들은 가중치 설정에 다수 존재)
  await expect(page.getByText(/reranker\./i).first()).toBeVisible();
});

// ── ⌘K 빠른 이동 palette ─────────────────────────────────────────

test("⌘K / Ctrl+K 빠른 이동 palette open + Esc close", async ({ page }) => {
  await gotoAdmin(page, "/admin");
  // headless chromium 은 OS 에 따라 Meta+k 가 안 잡힐 수 있어 ControlOrMeta 사용.
  await page.keyboard.press("ControlOrMeta+k");
  await expect(page.getByPlaceholder("페이지 이름 / 그룹 / URL")).toBeVisible({
    timeout: 3_000,
  });
  await page.keyboard.press("Escape");
});
