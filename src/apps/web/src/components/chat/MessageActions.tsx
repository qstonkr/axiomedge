"use client";

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
  return (
    <div className="mt-1 flex gap-1 text-xs opacity-0 group-hover:opacity-100">
      <button aria-label="출처 보기" onClick={onShowSources}>📎</button>
      <button aria-label="오너 찾기" onClick={onFindOwner}>👤</button>
      <button aria-label="재질문" onClick={onResubmit}>🔁</button>
      <button aria-label="오답 신고" onClick={onReportError}>⚠️</button>
      <button aria-label="복사" onClick={copy}>📋</button>
    </div>
  );
}
