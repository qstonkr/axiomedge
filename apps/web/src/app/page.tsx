/**
 * Root landing — placeholder for B-1 Day 2.
 *
 * The real (auth) and (app) trees are wired in Day 3+. For now this page
 * just confirms the design tokens compile and renders typography/spacing
 * correctly so we have a sanity check before committing.
 */
export default function Home() {
  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-24">
      <header className="space-y-3">
        <span className="inline-block rounded-pill bg-accent-subtle px-3 py-1 text-xs font-medium text-accent-emphasis">
          axiomedge web · B-1 Day 2
        </span>
        <h1 className="text-3xl font-semibold leading-tight text-fg-default">
          Frontend MVP 부트스트랩 완료
        </h1>
        <p className="text-sm leading-6 text-fg-muted">
          Linear + Notion 하이브리드 토큰이 적용된 Next.js 16 앱.
          Day 3 부터 BFF 인증과 6개 사용자 페이지를 차례로 추가합니다.
        </p>
      </header>

      <section className="rounded-lg border border-border-default bg-bg-canvas p-6 shadow-sm">
        <h2 className="text-lg font-medium text-fg-default">
          다음 단계
        </h2>
        <ul className="mt-3 space-y-2 text-sm text-fg-muted">
          <li>• Day 3 — BFF 인증 + AppShell layout</li>
          <li>• Day 4 — API client + UI primitives</li>
          <li>• Day 5–8 — 6개 사용자 페이지</li>
          <li>• Day 9–10 — i18n / 다크 모드 / Docker</li>
        </ul>
      </section>
    </main>
  );
}
