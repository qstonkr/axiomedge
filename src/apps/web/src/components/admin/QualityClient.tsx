"use client";

import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button, ErrorFallback, Skeleton, useToast } from "@/components/ui";
import { useTransparencyStats } from "@/hooks/admin/useLifecycle";
import {
  useDedupStats,
  useEvalHistory,
  useEvalStatus,
  useTriggerEval,
} from "@/hooks/admin/useQuality";
import type { EvalRun } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { KbQualityRadar } from "./KbQualityRadar";
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
  const transparency = useTransparencyStats();
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

  // 평가 history 의 metric 시계열 — 최근 → 오래된 순서로 reverse 해서 좌→우 시간순.
  const trendData = useMemo(() => {
    const rows = evalHistory.data?.items ?? [];
    return [...rows]
      .reverse()
      .map((r, idx) => ({
        idx: idx + 1,
        ts: (r.started_at ?? "").slice(5, 16).replace("T", " "),
        faithfulness: Number(((r.metrics?.faithfulness ?? 0) * 100).toFixed(1)),
        relevancy: Number(((r.metrics?.relevancy ?? 0) * 100).toFixed(1)),
        completeness: Number(((r.metrics?.completeness ?? 0) * 100).toFixed(1)),
      }));
  }, [evalHistory.data]);

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
        <h2 className="text-sm font-medium text-fg-default">투명성 지표</h2>
        {transparency.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-24" />
            <p className="text-[11px] text-fg-subtle">
              전체 Qdrant 컬렉션 스캔 중 — 첫 호출은 수십 초 걸릴 수 있습니다 (이후 5분간 캐시).
            </p>
          </div>
        ) : transparency.isError ? (
          <p className="text-xs text-fg-muted">
            투명성 지표를 불러올 수 없습니다.
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="투명성 점수"
              value={`${((transparency.data?.transparency_score ?? 0) * 100).toFixed(1)}%`}
              tone={
                (transparency.data?.transparency_score ?? 0) >= 0.9
                  ? "success"
                  : (transparency.data?.transparency_score ?? 0) >= 0.6
                    ? "warning"
                    : "danger"
              }
            />
            <MetricCard
              label="출처 커버리지"
              value={`${((transparency.data?.source_coverage_rate ?? 0) * 100).toFixed(1)}%`}
            />
            <MetricCard
              label="검증 완료 문서"
              value={(transparency.data?.verified ?? 0).toLocaleString()}
            />
            <MetricCard
              label="평균 출처 (응답당)"
              value={(transparency.data?.avg_sources_per_response ?? 0).toFixed(2)}
            />
          </div>
        )}
      </article>

      <article className="space-y-3">
        <h2 className="text-sm font-medium text-fg-default">KTS 6-Signal (KB 별)</h2>
        <KbQualityRadar />
      </article>

      <article className="space-y-3">
        <h2 className="text-sm font-medium text-fg-default">평가 메트릭 추이</h2>
        {evalHistory.isLoading ? (
          <Skeleton className="h-48" />
        ) : trendData.length < 2 ? (
          <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-6 text-center text-xs text-fg-muted">
            추이를 그리려면 평가 기록 2건 이상이 필요합니다.
          </p>
        ) : (
          <div
            className="rounded-lg border border-border-default bg-bg-canvas p-3"
            style={{ height: 260 }}
          >
            <ResponsiveContainer>
              <LineChart data={trendData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid stroke="var(--color-border-default)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="ts"
                  tick={{ fill: "var(--color-fg-subtle)", fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  domain={[0, 100]}
                  tick={{ fill: "var(--color-fg-subtle)", fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}%`}
                  width={40}
                />
                <RechartsTooltip
                  contentStyle={{
                    background: "var(--color-bg-canvas)",
                    border: "1px solid var(--color-border-default)",
                    borderRadius: 6,
                    fontSize: 12,
                    color: "var(--color-fg-default)",
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line
                  type="monotone"
                  dataKey="faithfulness"
                  name="Faithfulness"
                  stroke="var(--color-accent-default)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="relevancy"
                  name="Relevancy"
                  stroke="var(--color-success-default)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="completeness"
                  name="Completeness"
                  stroke="var(--color-warning-default)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </article>

      <article className="space-y-3">
        <h2 className="text-sm font-medium text-fg-default">최근 평가 history</h2>
        {evalHistory.isLoading ? (
          <Skeleton className="h-32" />
        ) : evalHistory.isError ? (
          <ErrorFallback
            title="평가 기록을 불러올 수 없습니다"
            error={evalHistory.error}
            onRetry={() => evalHistory.refetch()}
          />
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
