/**
 * Glossary 등록 — 다이얼로그 → 저장 → POST 200.
 *
 * 풀 CRUD (등록 → 검색 → 수정 → 삭제) 가 아니라 dominant 패턴
 * "다이얼로그 form 으로 mutation" 까지만 검증. 223k 행 glossary 의
 * client-side 필터가 첫 100 행만 보여줘서 새 용어 visual 검증이 비현실적.
 *
 * 기록되는 e2e 데이터는 globalTeardown 의 SQL sweep 가 정리.
 */
import { expect, test } from "@playwright/test";

import { gotoAdmin, loginAsAdmin, uniq } from "./helpers";

test.beforeEach(async ({ page }) => {
  await loginAsAdmin(page);
});

test("Glossary: 신규 용어 등록 (POST 200)", async ({ page }) => {
  const term = uniq("glossary-term");
  const definition = `e2e 자동 검증용 용어 — ${term}`;

  await gotoAdmin(page, "/admin/glossary");

  // 1. 신규 용어 dialog 열기 — glossary 223k 행 렌더가 끝날 때까지 button
  // 클릭이 캡처 안 될 수 있어 명시적 대기
  const newBtn = page.getByRole("button", { name: /\+ 신규 용어/ });
  await expect(newBtn).toBeEnabled({ timeout: 10_000 });
  await newBtn.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible({ timeout: 8_000 });

  // 2. KB 옵션 로드 후 첫 옵션 선택
  const kbSelect = dialog.locator("select").first();
  await expect
    .poll(async () => await kbSelect.locator("option").count(), {
      timeout: 8_000,
      message: "KB 옵션 로드 대기",
    })
    .toBeGreaterThan(1);
  const firstKbOption = await kbSelect.locator("option").nth(1).getAttribute("value");
  if (!firstKbOption) {
    test.skip(true, "활성 KB 가 없어 검증 불가");
    return;
  }
  await kbSelect.selectOption(firstKbOption);

  // 3. term + definition 입력
  await dialog.locator('input[required]').fill(term);
  await dialog.locator("textarea").first().fill(definition);

  // 4. 저장 — POST 응답 검증
  const saveBtn = dialog.getByRole("button", { name: "저장" });
  await expect(saveBtn).toBeEnabled({ timeout: 3_000 });
  const postResponse = page.waitForResponse(
    (r) =>
      r.url().includes("/api/proxy/api/v1/admin/glossary") &&
      r.request().method() === "POST",
    { timeout: 15_000 },
  );
  await saveBtn.click();
  const resp = await postResponse;
  expect(resp.status(), "POST /admin/glossary 200 반환").toBe(200);
  const body = (await resp.json()) as { success: boolean; term_id: string };
  expect(body.success).toBe(true);
  expect(body.term_id).toBeTruthy();
});
