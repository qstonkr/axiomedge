import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.PORT ?? "3000";
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? `http://localhost:${PORT}`;

/**
 * Playwright config — E2E smoke covering the 6 user-facing pages.
 *
 * The webServer block boots `next dev` on the configured port unless
 * PLAYWRIGHT_BASE_URL is set (CI typically points at a pre-launched build).
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  // admin CRUD 테스트 후 잔존 e2e-* 데이터 SQL 일괄 삭제. 단일 파일이라
  // admin 폴더 내부 경로 그대로 참조.
  globalTeardown: "./tests/e2e/admin/global-teardown.ts",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: "pnpm dev",
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
