"use client";

import { Skeleton } from "@/components/ui";
import { useAdminDashboardSummary } from "@/hooks/admin/useAdminDashboard";

import { MetricCard } from "./MetricCard";

function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString();
}

export function AdminDashboardClient() {
  const { data, isLoading, isError, error, refetch } = useAdminDashboardSummary();

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">운영 대시보드</h1>
        <p className="text-sm text-fg-muted">
          시스템 전반의 현재 상태 — 1분마다 자동 갱신.
        </p>
      </header>

      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, idx) => (
            <Skeleton key={idx} className="h-28" />
          ))}
        </div>
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            대시보드를 불러올 수 없습니다
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
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <MetricCard
              label="활성 KB"
              value={fmt(data?.active_kbs)}
              hint="status=active 인 모든 knowledge base"
            />
            <MetricCard
              label="문서 합계"
              value={fmt(data?.total_documents)}
              hint="활성 KB 의 document_count 합"
            />
            <MetricCard
              label="청크 합계"
              value={fmt(data?.total_chunks)}
              hint="검색 인덱스에 적재된 chunk 수"
            />
            <MetricCard
              label="대기중 피드백"
              value={fmt(data?.feedback_pending)}
              hint="status=pending 인 사용자 피드백"
              tone={(data?.feedback_pending ?? 0) > 10 ? "warning" : "neutral"}
            />
            <MetricCard
              label="대기중 오류 신고"
              value={fmt(data?.error_reports_pending)}
              hint="처리 안 된 오류 신고 — 운영팀 확인 필요"
              tone={
                (data?.error_reports_pending ?? 0) > 5 ? "danger" : "neutral"
              }
            />
            <MetricCard
              label="검색 (지난 24h)"
              value={fmt(data?.search_history_24h)}
              hint="search_log 의 최근 24시간 row 수"
            />
          </div>

          {data?.errors && data.errors.length > 0 && (
            <details className="rounded-md border border-warning-default/30 bg-warning-subtle p-3 text-xs">
              <summary className="cursor-pointer font-medium text-warning-default">
                일부 카운터 수집 실패 ({data.errors.length}건)
              </summary>
              <ul className="mt-2 list-disc space-y-1 pl-5 font-mono text-fg-muted">
                {data.errors.map((e, i) => (
                  <li key={i} className="break-words">
                    {e}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
