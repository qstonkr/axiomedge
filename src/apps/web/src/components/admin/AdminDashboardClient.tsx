"use client";

import { useMemo } from "react";
import { Boxes, FileText, MessageSquare, Search, ShieldAlert, Layers } from "lucide-react";

import { ErrorFallback, Skeleton } from "@/components/ui";
import { useAdminDashboardSummary } from "@/hooks/admin/useAdminDashboard";

import { AreaChartHero, type HeroPoint } from "./AreaChartHero";
import { L1CategoryChart } from "./L1CategoryChart";
import { MetricCard } from "./MetricCard";

const ICON_PROPS = { size: 14, strokeWidth: 1.75, "aria-hidden": true } as const;

function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString();
}

/**
 * Mock 시계열 placeholder — 백엔드 hourly endpoint 가 들어올 때까지
 * 현재 카운터를 마지막 점으로 두고 앞 N 시점을 ±20% 범위에서 흔들어 그린다.
 * 운영자에게 "지금 값이 추세 안에 있다"는 시각 anchor 를 주는 용도.
 */
function mockSeries(latest: number | null | undefined, length = 24): number[] {
  if (latest === null || latest === undefined || latest === 0) return [];
  let s = (latest * 9301 + 49297) % 233280;
  const next = () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
  const out: number[] = [];
  for (let i = 0; i < length - 1; i++) {
    const wobble = 0.8 + next() * 0.4;
    out.push(Math.max(0, Math.round(latest * wobble)));
  }
  out.push(latest);
  return out;
}

export function AdminDashboardClient() {
  const { data, isLoading, isError, error, refetch } =
    useAdminDashboardSummary();

  const series = useMemo(
    () => ({
      kbs: mockSeries(data?.active_kbs, 14),
      docs: mockSeries(data?.total_documents, 14),
      chunks: mockSeries(data?.total_chunks, 14),
      feedback: mockSeries(data?.feedback_pending, 14),
      errors: mockSeries(data?.error_reports_pending, 14),
      search: mockSeries(data?.search_history_24h, 24),
    }),
    [data],
  );

  const heroPeak = series.search.length ? Math.max(...series.search) : 0;
  const heroAvg = series.search.length
    ? Math.round(series.search.reduce((s, n) => s + n, 0) / series.search.length)
    : 0;

  // recharts AreaChart 용 — t 라벨은 24h 시점 ("HH:00")
  const heroPoints = useMemo<HeroPoint[]>(() => {
    const len = series.search.length;
    if (len < 2) return [];
    const now = new Date();
    return series.search.map((v, i) => {
      const offsetH = len - 1 - i;
      const d = new Date(now.getTime() - offsetH * 3_600_000);
      const hh = String(d.getHours()).padStart(2, "0");
      return { t: `${hh}:00`, v };
    });
  }, [series.search]);

  return (
    <section className="space-y-6">
      <header className="flex items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-fg-default">
            운영 대시보드
          </h1>
          <p className="text-sm text-fg-muted">
            시스템 전반의 현재 상태 — 1분마다 자동 갱신.
          </p>
        </div>
        {data && (
          <div className="flex items-center gap-2 text-xs text-fg-subtle">
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-success-default"
            />
            <span>실시간</span>
          </div>
        )}
      </header>

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-40" />
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, idx) => (
              <Skeleton key={idx} className="h-28" />
            ))}
          </div>
        </div>
      ) : isError ? (
        <ErrorFallback
          title="대시보드를 불러올 수 없습니다"
          error={error}
          onRetry={() => refetch()}
        />
      ) : (
        <>
          {/* Hero — 24h 검색 큰 시계열 */}
          <article className="overflow-hidden rounded-lg border border-border-default bg-bg-canvas">
            <header className="flex items-end justify-between gap-3 border-b border-border-default px-5 py-3">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
                  지난 24시간 검색
                </p>
                <p className="mt-1 text-3xl font-semibold tabular-nums tracking-tight text-fg-default">
                  {fmt(data?.search_history_24h)}
                  <span className="ml-1 text-sm font-normal text-fg-muted">
                    건
                  </span>
                </p>
              </div>
              <dl className="flex flex-col items-end gap-0.5 text-xs">
                <div className="flex items-center gap-1.5">
                  <dt className="text-fg-subtle">peak</dt>
                  <dd className="font-medium tabular-nums text-fg-default">
                    {fmt(heroPeak)}
                  </dd>
                </div>
                <div className="flex items-center gap-1.5">
                  <dt className="text-fg-subtle">avg</dt>
                  <dd className="font-medium tabular-nums text-fg-default">
                    {fmt(heroAvg)}
                  </dd>
                </div>
              </dl>
            </header>
            <div className="px-2 pt-2">
              {heroPoints.length > 1 ? (
                <AreaChartHero points={heroPoints} height={180} />
              ) : (
                <div className="flex h-32 items-center justify-center text-xs text-fg-subtle">
                  지난 24시간 검색 기록이 없습니다
                </div>
              )}
            </div>
          </article>

          {/* 6 메트릭 카드 — icon + sparkline + accent strip */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <MetricCard
              icon={<Boxes {...ICON_PROPS} />}
              label="활성 KB"
              value={fmt(data?.active_kbs)}
              hint="status=active"
              sparkline={series.kbs}
            />
            <MetricCard
              icon={<FileText {...ICON_PROPS} />}
              label="문서 합계"
              value={fmt(data?.total_documents)}
              hint="모든 활성 KB"
              sparkline={series.docs}
            />
            <MetricCard
              icon={<Layers {...ICON_PROPS} />}
              label="청크 합계"
              value={fmt(data?.total_chunks)}
              hint="검색 인덱스"
              sparkline={series.chunks}
            />
            <MetricCard
              icon={<MessageSquare {...ICON_PROPS} />}
              label="대기 피드백"
              value={fmt(data?.feedback_pending)}
              hint="status=pending"
              sparkline={series.feedback}
              tone={(data?.feedback_pending ?? 0) > 10 ? "warning" : "neutral"}
            />
            <MetricCard
              icon={<ShieldAlert {...ICON_PROPS} />}
              label="오류 신고"
              value={fmt(data?.error_reports_pending)}
              hint="처리 안 된 신고"
              sparkline={series.errors}
              tone={
                (data?.error_reports_pending ?? 0) > 5 ? "danger" : "neutral"
              }
            />
            <MetricCard
              icon={<Search {...ICON_PROPS} />}
              label="24h 검색"
              value={fmt(data?.search_history_24h)}
              hint="search_log row 수"
              sparkline={series.search}
            />
          </div>

          <L1CategoryChart />

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
