"use client";

import { useState } from "react";

import { Skeleton } from "@/components/ui";
import { useAgentTrace, useAgentTraces } from "@/hooks/admin/useQuality";
import type { AgentTraceListItem } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

export function TracesClient() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const list = useAgentTraces(50);
  const detail = useAgentTrace(selectedId);

  const traces = list.data?.traces ?? [];

  const columns: Column<AgentTraceListItem>[] = [
    {
      key: "trace_id",
      header: "Trace ID",
      render: (t) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {t.trace_id.slice(0, 8)}…
        </span>
      ),
    },
    {
      key: "query",
      header: "질의",
      render: (t) => (
        <span className="line-clamp-1 text-fg-default">{t.query}</span>
      ),
    },
    {
      key: "answer_preview",
      header: "답변 미리보기",
      render: (t) => (
        <span className="line-clamp-1 text-fg-muted">
          {t.answer_preview ?? "—"}
        </span>
      ),
    },
    {
      key: "iteration_count",
      header: "iter",
      align: "right",
      render: (t) => t.iteration_count ?? "—",
    },
    {
      key: "total_duration_ms",
      header: "지연",
      align: "right",
      render: (t) =>
        typeof t.total_duration_ms === "number"
          ? `${(t.total_duration_ms / 1000).toFixed(1)}s`
          : "—",
    },
    {
      key: "llm_provider",
      header: "Provider",
      render: (t) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {t.llm_provider ?? "—"}
        </span>
      ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">Agent Trace</h1>
        <p className="text-sm text-fg-muted">
          최근 50개 agentic 실행 trace. row 클릭 시 plan/iterations/critiques 상세 패널.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        <MetricCard label="총 trace" value={list.data?.count ?? 0} />
        <MetricCard
          label="평균 지연"
          value={
            traces.length > 0
              ? `${(
                  traces.reduce((s, t) => s + (t.total_duration_ms ?? 0), 0) /
                  traces.length /
                  1000
                ).toFixed(1)}s`
              : "—"
          }
        />
      </div>

      {list.isLoading ? (
        <Skeleton className="h-48" />
      ) : list.isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm text-danger-default">
          trace 목록을 불러올 수 없습니다
        </div>
      ) : (
        <DataTable<AgentTraceListItem>
          columns={columns}
          rows={traces}
          rowKey={(r) => r.trace_id}
          onRowClick={(r) => setSelectedId(r.trace_id)}
          empty="아직 agentic 실행 기록이 없습니다."
        />
      )}

      {selectedId && (
        <article className="space-y-3 rounded-lg border border-border-default bg-bg-canvas p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-fg-default">
              Trace {selectedId.slice(0, 8)}… 상세
            </h2>
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="text-xs text-fg-muted hover:text-fg-default"
            >
              닫기 ✕
            </button>
          </div>
          {detail.isLoading ? (
            <Skeleton className="h-40" />
          ) : detail.isError ? (
            <p className="text-xs text-danger-default">
              trace 를 불러올 수 없습니다 (만료 또는 조회 권한)
            </p>
          ) : (
            <pre className="max-h-[480px] overflow-auto rounded bg-bg-subtle p-3 font-mono text-[10px] leading-snug text-fg-default">
              {JSON.stringify(detail.data, null, 2)}
            </pre>
          )}
        </article>
      )}
    </section>
  );
}
