"use client";

import { useState, type FormEvent } from "react";
import { Plus } from "lucide-react";

import {
  Button,
  ErrorFallback,
  Input,
  Skeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import { useAgentTrace, useAgentTraces } from "@/hooks/admin/useQuality";
import { useAgenticAsk } from "@/hooks/useSearch";
import type { AgentTraceListItem } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

function NewAgenticForm({ onTraceId }: { onTraceId: (id: string) => void }) {
  const toast = useToast();
  const ask = useAgenticAsk();
  const [query, setQuery] = useState("");
  const [kbFilter, setKbFilter] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    const kbIds = kbFilter
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean);
    try {
      const res = await ask.mutateAsync({
        query: query.trim(),
        kb_ids: kbIds.length > 0 ? kbIds : null,
      });
      const tid = res.trace_id;
      if (tid) {
        toast.push(`실행 완료 — trace ${tid.slice(0, 8)}…`, "success");
        onTraceId(tid);
      } else {
        toast.push("실행 완료 (trace_id 없음)", "warning");
      }
      setQuery("");
    } catch (err) {
      toast.push(
        err instanceof Error ? err.message : "실행 실패",
        "danger",
      );
    }
  }

  return (
    <details className="rounded-lg border border-border-default bg-bg-canvas">
      <summary className="inline-flex cursor-pointer items-center gap-1.5 px-4 py-3 text-sm font-medium text-fg-default">
        <Plus size={14} strokeWidth={1.75} aria-hidden />
        새 agentic 질문 실행
      </summary>
      <form onSubmit={onSubmit} className="space-y-3 px-4 pb-4">
        <p className="text-xs text-fg-muted">
          여기에서 실행한 질문은 새 trace 를 생성하고, 그 결과를 아래
          trace 목록에서 즉시 확인할 수 있습니다.
        </p>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          질문
          <Textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            rows={2}
            placeholder="예: 신촌점 차주 매장 점검 일정"
            required
          />
        </label>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          KB 필터 (콤마 구분, 비우면 전체)
          <Input
            value={kbFilter}
            onChange={(e) => setKbFilter(e.target.value)}
            placeholder="예: g-espa, g-fc"
          />
        </label>
        <div className="flex justify-end">
          <Button type="submit" disabled={ask.isPending || !query.trim()}>
            {ask.isPending ? "실행 중…" : "실행"}
          </Button>
        </div>
      </form>
    </details>
  );
}

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

      <NewAgenticForm onTraceId={setSelectedId} />

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
        <ErrorFallback
          title="trace 목록을 불러올 수 없습니다"
          error={list.error}
          onRetry={() => list.refetch()}
        />
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
