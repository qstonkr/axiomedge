"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui";

/**
 * Per-route error boundary for the (app) tree. App-level layout still
 * renders (sidebar, header) — only the page slot is replaced.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surface to console — production should replace this with Sentry etc.
    console.error("App route error:", error);
  }, [error]);

  return (
    <section className="mx-auto flex max-w-2xl flex-col items-center gap-4 px-6 py-16 text-center">
      <span aria-hidden className="text-4xl">⚠️</span>
      <h1 className="text-xl font-semibold text-fg-default">
        페이지를 표시할 수 없습니다
      </h1>
      <p className="text-sm text-fg-muted">
        {error.message || "예기치 못한 오류가 발생했습니다."}
      </p>
      {error.digest && (
        <p className="font-mono text-xs text-fg-subtle">코드: {error.digest}</p>
      )}
      <div className="flex gap-2">
        <Button onClick={reset}>다시 시도</Button>
        <Button variant="ghost" onClick={() => (window.location.href = "/chat")}>
          홈으로
        </Button>
      </div>
    </section>
  );
}
