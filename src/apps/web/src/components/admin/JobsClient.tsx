"use client";

import { useState } from "react";

import { Button, Skeleton, useToast } from "@/components/ui";
import {
  useCancelIngestRun,
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
  const { data, isLoading, isError, error } = useIngestRuns();
  const cancel = useCancelIngestRun();
  const [cancelling, setCancelling] = useState<string | null>(null);
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
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">작업 모니터</h1>
        <p className="text-sm text-fg-muted">
          백그라운드 ingest run — 15초마다 자동 갱신.
        </p>
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
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            run 목록을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<IngestRun>
          columns={columns}
          rows={runs}
          rowKey={(r) => r.id}
          empty="실행된 run 이 없습니다."
        />
      )}
    </section>
  );
}
