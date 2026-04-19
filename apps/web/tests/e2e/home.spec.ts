import { expect, test } from "@playwright/test";

test("home page renders bootstrap heading (B-1 Day 2 smoke)", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /Frontend MVP 부트스트랩 완료/ }),
  ).toBeVisible();
});
