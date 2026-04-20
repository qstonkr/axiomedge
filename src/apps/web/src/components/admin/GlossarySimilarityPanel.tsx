"use client";

import { Skeleton } from "@/components/ui";
import { useGlossarySimilarityDistribution } from "@/hooks/admin/useContent";

import { MetricCard } from "./MetricCard";

/**
 * RapidFuzz 기반 용어 유사도 분포 — 운영자가 글로서리 품질 (중복 후보가
 * 얼마나 많은지) 한눈에. histogram bucket 을 가로 bar 로 표시
 * (recharts 없이 간단한 div width 로 — 의존성 최소화).
 */
export function GlossarySimilarityPanel() {
  const { data, isLoading } = useGlossarySimilarityDistribution();

  if (isLoading) return <Skeleton className="h-40" />;
  if (!data || data.error) {
    return (
      <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-6 text-center text-xs text-fg-muted">
        유사도 분포 데이터 없음 — 글로서리에 항목이 부족하거나 sampler 가
        실행되지 않음.
      </p>
    );
  }

  const dist = data.distribution ?? [];
  const max = dist.reduce((m, d) => Math.max(m, d.count), 0) || 1;

  return (
    <article className="space-y-3">
      <header className="space-y-1">
        <h3 className="text-sm font-medium text-fg-default">
          용어 유사도 분포
        </h3>
        <p className="text-xs text-fg-muted">
          RapidFuzz 로 random sample {data.sample_size} 쌍을 비교 → 0~1
          유사도 bucket 분포. 평균 {data.mean_similarity.toFixed(3)} ·
          전체 후보 쌍 {data.total_pairs.toLocaleString()}건.
        </p>
      </header>
      <div className="grid gap-3 sm:grid-cols-3">
        <MetricCard label="평균 유사도" value={data.mean_similarity.toFixed(3)} />
        <MetricCard
          label="후보 쌍"
          value={data.total_pairs.toLocaleString()}
        />
        <MetricCard label="샘플" value={data.sample_size.toLocaleString()} />
      </div>
      {dist.length > 0 ? (
        <div className="space-y-1.5 rounded-lg border border-border-default bg-bg-canvas p-4">
          {dist.map((b) => (
            <div
              key={b.bucket}
              className="grid grid-cols-[60px_minmax(0,1fr)_60px] items-center gap-3 text-xs"
            >
              <span className="font-mono text-fg-muted">{b.bucket}</span>
              <div className="h-3 overflow-hidden rounded bg-bg-subtle">
                <div
                  className="h-full bg-accent-default/70"
                  style={{ width: `${(b.count / max) * 100}%` }}
                  aria-label={`bucket ${b.bucket}: ${b.count}`}
                />
              </div>
              <span className="text-right font-mono tabular-nums text-fg-default">
                {b.count.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-6 text-center text-xs text-fg-muted">
          분포 데이터 없음
        </p>
      )}
    </article>
  );
}
