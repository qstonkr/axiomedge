"use client";

import { useState } from "react";

import { Button, Skeleton, useToast } from "@/components/ui";
import {
  useDedupStats,
  useEvalHistory,
  useEvalStatus,
  useTriggerEval,
} from "@/hooks/admin/useQuality";
import type { EvalRun } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

function fmtMetric(n: number | undefined | null): string {
  if (n === null || n === undefined) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

export function QualityClient() {
  const toast = useToast();
  const dedup = useDedupStats();
  const evalStatus = useEvalStatus();
  const evalHistory = useEvalHistory({ page: 1, page_size: 20 });
  const trigger = useTriggerEval();
  const [running, setRunning] = useState(false);

  async function onTriggerEval() {
    setRunning(true);
    try {
      const res = await trigger.mutateAsync({ kb_id: null });
      if (res.success) toast.push("평가가 시작되었습니다", "success");
      else toast.push("평가 시작 실패", "danger");
    } catch (e) {
      toast.push(
        e instanceof Error ? e.message : "평가 트리거 실패",
        "danger",
      );
    } finally {
      setRunning(false);
    }
  }

  const dedupData = dedup.data;

  const columns: Column<EvalRun>[] = [
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
        <span className="font-mono text-[10px] text-fg-muted">
          {r.kb_id || "전체"}
        </span>
      ),
    },
    {
      key: "status",
      header: "상태",
      render: (r) => (
        <SeverityBadge level={statusToSeverity(r.status)}>
          {r.status || "unknown"}
        </SeverityBadge>
      ),
    },
    {
      key: "faithfulness",
      header: "Faithfulness",
      align: "right",
      render: (r) => fmtMetric(r.metrics?.faithfulness),
    },
    {
      key: "relevancy",
      header: "Relevancy",
      align: "right",
      render: (r) => fmtMetric(r.metrics?.relevancy),
    },
    {
      key: "completeness",
      header: "Completeness",
      align: "right",
      render: (r) => fmtMetric(r.metrics?.completeness),
    },
    {
      key: "source_recall",
      header: "Source Recall",
      align: "right",
      render: (r) => fmtMetric(r.metrics?.source_recall),
    },
  ];

  const evalRunning = evalStatus.data?.status === "running";

  return (
    <section className="space-y-6">
      <header className="flex items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-fg-default">RAG 품질</h1>
          <p className="text-sm text-fg-muted">
            Golden set 기반 평가 + dedup 파이프라인 통계.
          </p>
        </div>
        <Button
          onClick={onTriggerEval}
          disabled={running || evalRunning}
          size="md"
        >
          {evalRunning
            ? `평가 중… (${Math.round((evalStatus.data?.progress ?? 0) * 100)}%)`
            : running
              ? "시작 중…"
              : "전체 평가 실행"}
        </Button>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="평가 상태"
          value={
            <SeverityBadge level={statusToSeverity(evalStatus.data?.status)}>
              {evalStatus.data?.status || "—"}
            </SeverityBadge>
          }
        />
        <MetricCard
          label="중복 탐지 (전체)"
          value={dedupData?.total_duplicates_found ?? "—"}
        />
        <MetricCard
          label="해결됨"
          value={dedupData?.total_resolved ?? "—"}
          tone="success"
        />
        <MetricCard
          label="대기 중"
          value={dedupData?.pending ?? "—"}
          tone={(dedupData?.pending ?? 0) > 0 ? "warning" : "neutral"}
        />
      </div>

      <article className="space-y-3">
        <h2 className="text-sm font-medium text-fg-default">최근 평가 history</h2>
        {evalHistory.isLoading ? (
          <Skeleton className="h-32" />
        ) : evalHistory.isError ? (
          <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm text-danger-default">
            평가 기록을 불러올 수 없습니다
          </div>
        ) : (
          <DataTable<EvalRun>
            columns={columns}
            rows={evalHistory.data?.items ?? []}
            rowKey={(r, idx) => r.id ?? r.eval_id ?? `eval-${idx}`}
            empty="아직 평가 기록이 없습니다. 우상단 '전체 평가 실행' 으로 시작."
          />
        )}
      </article>
    </section>
  );
}
