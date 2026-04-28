"use client";

import { useEffect, useState } from "react";

const KEY = "axe-privacy-consent-v1";

export function PrivacyConsent() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    setOpen(localStorage.getItem(KEY) !== "accepted");
  }, []);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="max-w-md rounded-lg border border-border-default bg-bg-default p-6 shadow-xl">
        <h2 className="text-lg font-semibold">처리방침 안내</h2>
        <p className="mt-3 text-sm text-fg-muted">
          AI 검색 시 입력하신 질의·답변은 시스템 개선·감사 목적으로{" "}
          <b>90일 보관 후 자동 파기</b>됩니다. 본인 대화는 좌측 사이드바에서
          직접 삭제할 수 있습니다.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <a
            href="/security#chat-retention"
            className="text-xs text-fg-muted underline"
          >
            상세 처리방침
          </a>
          <button
            onClick={() => {
              localStorage.setItem(KEY, "accepted");
              setOpen(false);
            }}
            className="rounded-md bg-fg-default px-3 py-1.5 text-sm text-bg-default"
          >
            동의하고 시작
          </button>
        </div>
      </div>
    </div>
  );
}
