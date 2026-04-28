/**
 * Chat redesign E2E — exercises full /chat flow + redirects.
 *
 * Requires the FastAPI server (`make api`) and arq worker to be running,
 * with admin@knowledge.local seeded (CHAT_AUTH_ENABLED=true,
 * AUTH_ADMIN_INITIAL_PASSWORD=dev1234!).
 *
 * Run: cd src/apps/web && pnpm test:e2e tests/e2e/chat-flow.spec.ts
 */
import { expect, test } from "@playwright/test";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill("admin@knowledge.local");
  await page.getByLabel(/password/i).fill("dev1234!");
  await page.getByRole("button", { name: /로그인|login/i }).click();
  await page.waitForURL(/\/chat|\/$|\/find-owner|\/my-/);
}

test("/find-owner redirects to /chat with onboarding banner", async ({ page }) => {
  await login(page);
  await page.goto("/find-owner");
  await expect(page).toHaveURL(/\/chat\?onboarding=owner/);
  await expect(page.getByText(/\/owner/)).toBeVisible();
});

test("/search-history redirects to /chat", async ({ page }) => {
  await login(page);
  await page.goto("/search-history");
  await expect(page).toHaveURL(/\/chat$/);
});

test("full chat: new → message → sidebar refresh → rename → delete", async ({ page }) => {
  await login(page);
  await page.goto("/chat");

  // Dismiss privacy consent if shown (first-login)
  const consent = page.getByRole("button", { name: /동의/ });
  if (await consent.isVisible().catch(() => false)) await consent.click();

  // Click + 새 대화
  await page.getByRole("button", { name: "+ 새 대화" }).click();

  // Type and send
  await page.getByPlaceholder(/질문/).fill("안녕");
  await page.keyboard.press("ControlOrMeta+Enter");

  // Wait for assistant turn (any text in message stream)
  await expect(page.locator("ul li").first()).toBeVisible({ timeout: 30_000 });

  // Sidebar shows the new conversation (auto-title may take seconds; just verify count > 0).
  const items = page.locator(
    'aside button:has-text("(제목 없음)"), aside [class*="rounded-md"]:has(button)',
  );
  await expect(items.first()).toBeVisible();

  // Rename via hover icon
  await items.first().hover();
  await page.getByLabel("이름 변경").first().click();
  await page.keyboard.type("E2E 테스트 대화");
  await page.keyboard.press("Enter");
  await expect(page.getByText("E2E 테스트 대화")).toBeVisible();

  // Delete (browser confirm() auto-accepted)
  page.once("dialog", (d) => d.accept());
  await page.getByText("E2E 테스트 대화").hover();
  await page.getByLabel("삭제").first().click();
  await expect(page.getByText("E2E 테스트 대화")).toBeHidden();
});
