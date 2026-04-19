"use client";

import { Skeleton } from "@/components/ui";
import { usePipelineStatus } from "@/hooks/admin/useContent";

import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

export function IngestClient() {
  const { data, isLoading, isError, error } = usePipelineStatus();

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">Ingest 작업</h1>
        <p className="text-sm text-fg-muted">
          파이프라인 실행 상태 + 최근 run + 큐 — 15초마다 자동 갱신.
        </p>
      </header>

      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, idx) => (
            <Skeleton key={idx} className="h-24" />
          ))}
        </div>
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            파이프라인 상태를 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <MetricCard
              label="실행 중"
              value={data?.active_runs ?? 0}
              tone={(data?.active_runs ?? 0) > 0 ? "warning" : "neutral"}
            />
            <MetricCard
              label="대기열"
              value={data?.queued ?? 0}
              tone={(data?.queued ?? 0) > 5 ? "warning" : "neutral"}
            />
            <MetricCard
              label="전체 상태"
              value={
                <SeverityBadge level={statusToSeverity(data?.status)}>
                  {data?.status || "unknown"}
                </SeverityBadge>
              }
            />
          </div>

          {data?.last_run && (
            <article className="rounded-lg border border-border-default bg-bg-canvas p-4">
              <h2 className="mb-3 text-sm font-medium text-fg-default">
                최근 실행
              </h2>
              <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                <div>
                  <dt className="text-fg-muted">KB</dt>
                  <dd className="mt-0.5 font-mono text-fg-default">
                    {data.last_run.kb_id || "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-fg-muted">소스</dt>
                  <dd className="mt-0.5 text-fg-default">
                    {data.last_run.source_name || "—"}{" "}
                    <span className="text-fg-subtle">
                      ({data.last_run.source_type || "—"})
                    </span>
                  </dd>
                </div>
                <div>
                  <dt className="text-fg-muted">상태</dt>
                  <dd className="mt-0.5">
                    <SeverityBadge level={statusToSeverity(data.last_run.status)}>
                      {data.last_run.status || "unknown"}
                    </SeverityBadge>
                  </dd>
                </div>
                <div>
                  <dt className="text-fg-muted">시작</dt>
                  <dd className="mt-0.5 font-mono text-fg-default">
                    {fmtDate(data.last_run.started_at)}
                  </dd>
                </div>
                <div className="sm:col-span-4">
                  <dt className="text-fg-muted">Run ID</dt>
                  <dd className="mt-0.5 break-all font-mono text-xs text-fg-subtle">
                    {data.last_run.id || data.last_run.run_id || "—"}
                  </dd>
                </div>
                {data.last_run.error_message && (
                  <div className="sm:col-span-4">
                    <dt className="text-fg-muted">오류</dt>
                    <dd className="mt-0.5 whitespace-pre-wrap font-mono text-xs text-danger-default">
                      {data.last_run.error_message}
                    </dd>
                  </div>
                )}
              </dl>
            </article>
          )}
        </>
      )}
    </section>
  );
}
