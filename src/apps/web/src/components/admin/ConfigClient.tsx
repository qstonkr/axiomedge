"use client";

import { Skeleton } from "@/components/ui";
import { useConfigWeights } from "@/hooks/admin/useOps";

export function ConfigClient() {
  const { data, isLoading, isError, error, refetch } = useConfigWeights();

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">가중치 설정</h1>
        <p className="text-sm text-fg-muted">
          검색 파이프라인 가중치 + 임계값. 현재는 read-only 뷰 (편집은 후속).
          변경은 <code className="font-mono text-xs">config/weights.yaml</code>{" "}
          파일 직접 수정 + restart.
        </p>
      </header>

      {isLoading ? (
        <Skeleton className="h-96" />
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            가중치를 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
          <button
            type="button"
            onClick={() => refetch()}
            className="mt-3 rounded-md border border-border-default px-3 py-1 text-xs text-fg-default hover:bg-bg-muted"
          >
            다시 시도
          </button>
        </div>
      ) : (
        <article className="rounded-lg border border-border-default bg-bg-canvas p-4">
          <pre className="max-h-[640px] overflow-auto font-mono text-[10px] leading-snug text-fg-default">
            {JSON.stringify(data, null, 2)}
          </pre>
        </article>
      )}
    </section>
  );
}
