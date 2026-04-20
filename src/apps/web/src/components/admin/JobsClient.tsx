"use client";

import { useState } from "react";

import {
  Button,
  ErrorFallback,
  Skeleton,
  useToast,
} from "@/components/ui";
import {
  useCancelIngestRun,
  useIngestRunDetail,
  useIngestRuns,
} from "@/hooks/admin/useOps";
import type { IngestRun } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

export function JobsClient() {
  const toast = useToast();
  const [autoRefresh, setAutoRefresh] = useState(true);
  const { data, isLoading, isError, error, refetch, isFetching, dataUpdatedAt } =
    useIngestRuns(autoRefresh);
  const cancel = useCancelIngestRun();
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const runs = data ?? [];

  async function onCancel(r: IngestRun) {
    if (!confirm(`'${r.source_name ?? r.kb_id}' run 을 취소하시겠습니까?`)) return;
    setCancelling(r.id);
    try {
      await cancel.mutateAsync(r.id);
      toast.push("취소 요청됨", "success");
    } catch (e) {
      toast.push(
        e instanceof Error ? e.message : "취소 요청 실패",
        "danger",
      );
    } finally {
      setCancelling(null);
    }
  }

  const counts = {
    running: runs.filter((r) => r.status === "running").length,
    completed: runs.filter((r) => r.status === "completed").length,
    failed: runs.filter((r) => r.status === "failed").length,
  };

  const columns: Column<IngestRun>[] = [
    {
      key: "started_at",
      header: "시작",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(r.started_at)}
        </span>
      ),
    },
    {
      key: "kb_id",
      header: "KB",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-muted">{r.kb_id}</span>
      ),
    },
    {
      key: "source_name",
      header: "소스",
      render: (r) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg-default">
            {r.source_name ?? "—"}
          </span>
          <span className="text-[10px] text-fg-subtle">{r.source_type ?? "—"}</span>
        </div>
      ),
    },
    {
      key: "status",
      header: "상태",
      render: (r) => (
        <SeverityBadge level={statusToSeverity(r.status)}>
          {r.status ?? "—"}
        </SeverityBadge>
      ),
    },
    {
      key: "documents_ingested",
      header: "수집/적재",
      align: "right",
      render: (r) => (
        <span className="text-fg-default">
          {r.documents_fetched ?? 0} / {r.documents_ingested ?? 0}
        </span>
      ),
    },
    {
      key: "chunks_stored",
      header: "청크",
      align: "right",
      render: (r) => (r.chunks_stored ?? 0).toLocaleString(),
    },
    {
      key: "_detail",
      header: "",
      render: (r) => (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setExpanded((cur) => (cur === r.id ? null : r.id))}
        >
          {expanded === r.id ? "접기" : "상세"}
        </Button>
      ),
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (r) =>
        r.status === "running" ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={cancelling === r.id}
            onClick={() => onCancel(r)}
          >
            {cancelling === r.id ? "…" : "취소"}
          </Button>
        ) : (
          <span className="text-fg-subtle">—</span>
        ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-fg-default">작업 모니터</h1>
          <p className="text-sm text-fg-muted">
            백그라운드 ingest run — {autoRefresh ? "15초마다 자동 갱신" : "자동 갱신 꺼짐"}.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          {dataUpdatedAt > 0 && (
            <span className="text-fg-subtle">
              최근 갱신{" "}
              {new Date(dataUpdatedAt).toLocaleTimeString("ko-KR", {
                hour12: false,
              })}
            </span>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => refetch()}
            disabled={isFetching}
            title="지금 한 번 새로고침"
          >
            🔄 {isFetching ? "갱신 중…" : "새로고침"}
          </Button>
          <label className="inline-flex items-center gap-1.5 cursor-pointer rounded-md border border-border-default px-2.5 py-1 text-fg-muted">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="h-3.5 w-3.5 accent-accent-default"
            />
            <span>자동 갱신</span>
          </label>
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <MetricCard
          label="실행 중"
          value={counts.running}
          tone={counts.running > 0 ? "warning" : "neutral"}
        />
        <MetricCard label="완료" value={counts.completed} tone="success" />
        <MetricCard
          label="실패"
          value={counts.failed}
          tone={counts.failed > 0 ? "danger" : "neutral"}
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <ErrorFallback
          title="run 목록을 불러올 수 없습니다"
          error={error}
          onRetry={() => refetch()}
        />
      ) : (
        <DataTable<IngestRun>
          columns={columns}
          rows={runs}
          rowKey={(r) => r.id}
          empty="실행된 run 이 없습니다."
        />
      )}

      {expanded && (
        <RunDetailPanel
          runId={expanded}
          onClose={() => setExpanded(null)}
        />
      )}
    </section>
  );
}

/**
 * 한 ingestion run 의 status_logs (step 별 outcome) expander.
 * Streamlit `job_monitor.py` 의 expander 패턴 이식.
 */
function RunDetailPanel({
  runId,
  onClose,
}: {
  runId: string;
  onClose: () => void;
}) {
  const detail = useIngestRunDetail(runId);
  const data = detail.data;
  const logs = data?.status_logs ?? [];

  return (
    <article className="space-y-3 rounded-lg border border-border-default bg-bg-canvas p-4">
      <header className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-fg-default">
            Run 상세 — {runId.slice(0, 8)}…
          </h2>
          {data?.kb_id && (
            <p className="text-xs text-fg-muted">
              KB: <code className="font-mono">{data.kb_id}</code>
              {data.source_type && ` · ${data.source_type}`}
            </p>
          )}
        </div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          닫기 ✕
        </Button>
      </header>

      {detail.isLoading ? (
        <Skeleton className="h-32" />
      ) : detail.isError ? (
        <ErrorFallback
          title="상세를 불러올 수 없습니다"
          error={detail.error}
          onRetry={() => detail.refetch()}
        />
      ) : (
        <>
          {data?.error_message && (
            <p className="rounded-md border border-danger-default/30 bg-danger-subtle p-3 font-mono text-xs whitespace-pre-wrap text-danger-default">
              {data.error_message}
            </p>
          )}
          {logs.length === 0 ? (
            <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-6 text-center text-xs text-fg-muted">
              status_logs 가 비어있습니다 (이전 단계 정보 없음).
            </p>
          ) : (
            <ol className="space-y-1.5 border-l-2 border-border-default pl-4">
              {logs.map((log, idx) => (
                <li
                  key={`${log.step ?? "?"}-${idx}`}
                  className="grid grid-cols-[20px_140px_minmax(0,1fr)] items-start gap-3 text-xs"
                >
                  <span aria-hidden className="-ml-[26px] text-base">
                    {log.status === "completed" ? "✅" : log.status === "failed" ? "❌" : "⚙️"}
                  </span>
                  <span className="font-mono text-fg-muted">
                    {log.timestamp?.slice(0, 19).replace("T", " ") ?? "—"}
                  </span>
                  <span>
                    <span className="rounded bg-bg-muted px-1.5 py-0.5 font-mono text-[10px] text-fg-default">
                      {log.step ?? "?"}
                    </span>
                    <span className="ml-2 text-fg-default">
                      {log.message ?? "—"}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </article>
  );
}
