"use client";

import { Sparkles } from "lucide-react";

import { usePopularQueries } from "@/hooks/useSearch";

const FALLBACK_SUGGESTIONS = [
  "점포 운영 절차",
  "정산 프로세스",
  "분쟁 조정 방법",
  "주간보고 내용",
] as const;

/**
 * 추천 검색어 — vertical list cards.
 *
 * 이전 (PR4 of UX): `<Button size="sm">` (h-8 fixed) + grid 4 cols 라 긴 한국어
 * 질문이 wrap 시 텍스트가 button bound 를 벗어나 section title 위로 overlap
 * 하던 버그가 있었음 (디자인 검토 발견). Card 기반 vertical list 로 전환:
 * - auto-height 라 wrap 안전
 * - 모바일/데스크탑 동일 layout (consistency)
 * - 긴 한국어 질문 (30+ chars) 도 가독성 ↑
 */
export function RecommendedQueries({
  onPick,
  pending,
}: {
  onPick: (q: string) => void;
  pending: boolean;
}) {
  const popular = usePopularQueries(7, 4);
  const queries =
    popular.data?.queries && popular.data.queries.length > 0
      ? popular.data.queries
      : FALLBACK_SUGGESTIONS;
  const isFallback =
    !popular.data?.queries || popular.data.queries.length === 0;

  return (
    <div className="space-y-3">
      <h3 className="flex items-center gap-1.5 text-xs font-medium text-fg-muted">
        <Sparkles aria-hidden size={14} strokeWidth={1.75} />
        <span>이런 것을 검색해보세요</span>
        {!isFallback && (
          <span className="font-normal text-fg-subtle">
            · 지난 {popular.data!.days}일 인기
          </span>
        )}
      </h3>
      <ul className="space-y-1">
        {queries.map((q) => (
          <li key={q}>
            <button
              type="button"
              onClick={() => onPick(q)}
              disabled={pending}
              className="block w-full rounded-md border border-border-default bg-bg-canvas px-3 py-2 text-left text-sm leading-relaxed text-fg-default transition-colors hover:border-border-strong hover:bg-bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            >
              {q}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
