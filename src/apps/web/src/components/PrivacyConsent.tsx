"use client";

import { useEffect, useState } from "react";

const KEY = "axe-privacy-consent-v1";
const POLICY_VERSION = "v1";

type ConsentRecord = {
  policy_version: string;
  accepted_at: string;
  withdrawn_at: string | null;
  is_active: boolean;
};

/**
 * First-login + post-withdrawal modal. The server is the source of truth —
 * we fetch the current consent on mount. The localStorage flag is used as
 * a *cache-only hint* so we can render instantly on subsequent loads
 * without a flash, but the server response always wins:
 * - server says active   → close modal, write localStorage flag
 * - server says withdrawn or null → open modal, clear localStorage flag
 */
export function PrivacyConsent() {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    // Optimistic — show no modal if a previous session already accepted,
    // then verify against the server.
    const cached = localStorage.getItem(KEY) === "accepted";
    setOpen(!cached);

    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/proxy/api/v1/users/me/consent", {
          method: "GET",
        });
        if (!res.ok) return;
        const data = (await res.json()) as ConsentRecord | null;
        if (cancelled) return;
        if (data && data.is_active) {
          localStorage.setItem(KEY, "accepted");
          setOpen(false);
        } else {
          // null (never accepted) OR withdrawn — re-prompt.
          localStorage.removeItem(KEY);
          setOpen(true);
        }
      } catch {
        // Network failure — fall back to localStorage hint, no modal flash.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (!open) return null;

  async function accept() {
    setSubmitting(true);
    try {
      await fetch("/api/proxy/api/v1/users/me/consent", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ policy_version: POLICY_VERSION }),
      });
    } catch {
      // network errors non-blocking
    }
    localStorage.setItem(KEY, "accepted");
    setSubmitting(false);
    setOpen(false);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="privacy-consent-title"
        aria-describedby="privacy-consent-body"
        className="max-w-md rounded-lg border border-border-default bg-bg-canvas p-6 shadow-lg"
      >
        <h2 id="privacy-consent-title" className="text-lg font-semibold">
          처리방침 안내
        </h2>
        <p id="privacy-consent-body" className="mt-3 text-sm text-fg-muted">
          AI 검색 시 입력하신 질의·답변은 시스템 개선·감사 목적으로{" "}
          <b className="text-fg-default">90일 보관 후 자동 파기</b>됩니다. 본인 대화는
          좌측 사이드바에서 직접 삭제할 수 있고, 동의는 언제든 철회할 수
          있습니다 (PIPA §37).
        </p>
        <div className="mt-4 flex items-center justify-end gap-3">
          <a
            href="/security#chat-retention"
            className="text-xs text-fg-muted underline hover:text-fg-default focus-visible:outline-2 focus-visible:outline-accent-default"
          >
            상세 처리방침
          </a>
          <button
            type="button"
            autoFocus
            disabled={submitting}
            onClick={accept}
            className="rounded-md bg-fg-default px-3 py-1.5 text-sm text-bg-canvas hover:bg-accent-default disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-accent-default"
          >
            {submitting ? "기록 중…" : "동의하고 시작"}
          </button>
        </div>
      </div>
    </div>
  );
}
