"use client";

import { Button } from "@/components/ui";

const SUGGESTIONS = [
  "점포 운영 절차",
  "정산 프로세스",
  "분쟁 조정 방법",
  "주간보고 내용",
] as const;

/**
 * 채팅이 비어 있을 때 (또는 사용자 요청 시) 표시되는 추천 검색어 4개.
 * Streamlit `chat.py` 의 `_suggestions` 동치 — hardcoded list.
 * 추후 backend 가 popular queries / personalized 추천을 줄 때 동적 list 로 교체 가능.
 */
export function RecommendedQueries({
  onPick,
  pending,
}: {
  onPick: (q: string) => void;
  pending: boolean;
}) {
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-fg-muted">
        💡 이런 것을 검색해보세요
      </h3>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {SUGGESTIONS.map((q) => (
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
