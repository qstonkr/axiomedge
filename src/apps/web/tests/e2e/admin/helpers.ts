/**
 * 공통 admin e2e 헬퍼.
 *
 * 모든 admin 테스트가 다음 환경을 가정한다:
 *   - FastAPI (`make api`) + arq worker 실행 중
 *   - admin@knowledge.local 계정 (CHAT_AUTH_ENABLED=true,
 *     AUTH_ADMIN_INITIAL_PASSWORD=dev1234!) 시드됨
 *   - default-org 가 활성 조직
 *
 * 테스트 데이터는 `e2e-{ts}` prefix 로 만들고, UI delete 로 self-cleanup.
 * 크래시로 남은 잔존 데이터는 globalTeardown 이 SQL 로 일괄 정리.
 */
import type { Page } from "@playwright/test";

export const E2E_PREFIX = `e2e-${Date.now()}`;

export function uniq(label: string): string {
  return `${E2E_PREFIX}-${label}-${Math.random().toString(36).slice(2, 6)}`;
}

/** admin 계정으로 로그인. consent 다이얼로그 자동 동의. */
export async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel(/이메일|email/i).fill("admin@knowledge.local");
  await page.getByLabel(/비밀번호|password/i).fill("dev1234!");
  await page.getByRole("button", { name: /로그인$/i }).click();
  await page.waitForURL(/\/chat|\/$|\/my-/, { timeout: 15_000 });
  // 첫 로그인 시 consent 모달 — 있으면 동의
  const consent = page.getByRole("button", { name: /동의/ });
  if (await consent.isVisible().catch(() => false)) {
    await consent.click();
  }
}

/**
 * /admin 으로 이동 (이미 로그인 상태 가정).
 *
 * networkidle 가 아니라 domcontentloaded 사용 — glossary (223k 용어 polling),
 * quality (sparkline / 30s polling), graph (Neo4j stat refresh) 등은
 * networkidle 가 영원히 안 잡힘. SPA 가 mount 되면 domcontentloaded fired.
 * mount 후 핵심 element 표시까지는 expect().toBeVisible(timeout) 으로 기다림.
 */
export async function gotoAdmin(page: Page, path = "/admin"): Promise<void> {
  await page.goto(path, { waitUntil: "domcontentloaded", timeout: 20_000 });
}
