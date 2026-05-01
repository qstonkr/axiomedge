"use client";

import { AlertTriangle, Copy, Paperclip, RotateCcw, User } from "lucide-react";

const ACTION_BTN =
  "inline-flex h-7 w-7 items-center justify-center rounded text-fg-muted opacity-70 transition hover:bg-bg-muted hover:text-fg-default hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-accent-default";

export function MessageActions({
  content, onShowSources, onFindOwner, onResubmit, onReportError,
}: {
  content: string;
  onShowSources: () => void;
  onFindOwner: () => void;
  onResubmit: () => void;
  onReportError: () => void;
}) {
  async function copy() {
    await navigator.clipboard.writeText(content);
  }
  // Always-visible action bar — hover-only opacity-0 was a regression that
  // broke discoverability (touch + keyboard users never saw the actions).
  // Default state is muted (opacity-70); hover/focus brings full strength.
  return (
    <div className="mt-2 flex gap-1" role="toolbar" aria-label="메시지 액션">
      <button type="button" aria-label="출처 보기" className={ACTION_BTN} onClick={onShowSources}>
        <Paperclip aria-hidden size={14} strokeWidth={1.75} />
      </button>
      <button type="button" aria-label="오너 찾기" className={ACTION_BTN} onClick={onFindOwner}>
        <User aria-hidden size={14} strokeWidth={1.75} />
      </button>
      <button type="button" aria-label="재질문" className={ACTION_BTN} onClick={onResubmit}>
        <RotateCcw aria-hidden size={14} strokeWidth={1.75} />
      </button>
      <button type="button" aria-label="오답 신고" className={ACTION_BTN} onClick={onReportError}>
        <AlertTriangle aria-hidden size={14} strokeWidth={1.75} />
      </button>
      <button type="button" aria-label="복사" className={ACTION_BTN} onClick={copy}>
        <Copy aria-hidden size={14} strokeWidth={1.75} />
      </button>
    </div>
  );
}
