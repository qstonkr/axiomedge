"use client";

import { Button } from "@/components/ui";
import { usePopularQueries } from "@/hooks/useSearch";

const FALLBACK_SUGGESTIONS = [
  "점포 운영 절차",
  "정산 프로세스",
  "분쟁 조정 방법",
  "주간보고 내용",
] as const;

/**
 * 추천 검색어 — backend `popular-queries` (지난 7일 top 4) 우선, 결과 비어
 * 있거나 fetch 실패 시 hardcoded fallback. Streamlit `chat.py` 의 동치 +
 * 데이터 기반 personalize.
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
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-fg-muted">
        💡 이런 것을 검색해보세요
        {!isFallback && (
          <span className="ml-1 font-normal text-fg-subtle">
            (지난 {popular.data!.days}일 인기)
          </span>
        )}
      </h3>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {queries.map((q) => (
          <Button
            key={q}
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onPick(q)}
            disabled={pending}
            className="justify-start text-left"
          >
            {q}
          </Button>
        ))}
      </div>
    </div>
  );
}
