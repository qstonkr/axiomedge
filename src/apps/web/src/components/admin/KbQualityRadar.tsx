"use client";

import { useMemo, useState } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

import { Select, Skeleton } from "@/components/ui";
import {
  useKbTrustDistribution,
  useKbTrustScores,
} from "@/hooks/admin/useQuality";
import { useSearchableKbs } from "@/hooks/useSearch";
import type { TrustScoreItem } from "@/lib/api/endpoints";

import { MetricCard } from "./MetricCard";

/**
 * KTS 6-Signal 정의 — Streamlit constants.py 의 `KTS_SIGNALS` 와 동일.
 * 각 signal 의 backend field 와 화면 라벨 매핑.
 */
const KTS_SIGNALS = [
  { key: "accuracy", label: "정확도", field: "hallucination_score" },
  {
    key: "source_credibility",
    label: "출처 신뢰도",
    field: "source_credibility",
  },
  { key: "freshness", label: "신선도", field: "freshness_score" },
  { key: "consistency", label: "일관성", field: "consistency_score" },
  { key: "usage_feedback", label: "사용자 피드백", field: "usage_score" },
  {
    key: "expert_validation",
    label: "전문가 검증",
    field: "user_validation_score",
  },
] as const;

const TIER_TONES: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  HIGH: "success",
  MEDIUM: "warning",
  LOW: "danger",
  UNCERTAIN: "neutral",
  UNVERIFIED: "neutral",
};

function average(items: TrustScoreItem[], field: keyof TrustScoreItem): number {
  if (items.length === 0) return 0;
  const vals = items
    .map((it) => Number(it[field] ?? 0))
    .filter((n) => Number.isFinite(n));
  if (vals.length === 0) return 0;
  return vals.reduce((s, n) => s + n, 0) / vals.length;
}

export function KbQualityRadar() {
  const kbs = useSearchableKbs();
  const [kbId, setKbId] = useState("");
  const scores = useKbTrustScores(kbId || null);
  const dist = useKbTrustDistribution(kbId || null);

  const chartData = useMemo(() => {
    const items = scores.data?.items ?? [];
    return KTS_SIGNALS.map((s) => ({
      signal: s.label,
      value: Number(
        average(items, s.field as keyof TrustScoreItem).toFixed(3),
      ),
    }));
  }, [scores.data]);

  const total = scores.data?.total ?? 0;
  const avg = dist.data?.avg_score ?? 0;
  const distTiers = dist.data?.distribution ?? {};

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border-default bg-bg-canvas p-4">
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          KB 선택
          <Select value={kbId} onChange={(e) => setKbId(e.target.value)}>
            <option value="">— KB 선택 —</option>
            {(kbs.data ?? []).map((kb) => (
              <option key={kb.kb_id} value={kb.kb_id}>
                {kb.name} ({kb.kb_id})
              </option>
            ))}
          </Select>
        </label>
      </div>

      {!kbId ? (
        <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-8 text-center text-xs text-fg-muted">
          KB 를 선택하면 KTS 6-Signal 레이더가 표시됩니다.
        </p>
      ) : scores.isLoading ? (
        <Skeleton className="h-80" />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <MetricCard label="문서 수" value={total.toLocaleString()} />
            <MetricCard
              label="평균 KTS"
              value={(avg * 100).toFixed(1) + "%"}
              tone={avg >= 0.7 ? "success" : avg >= 0.4 ? "warning" : "danger"}
            />
            {Object.entries(distTiers).map(([tier, n]) => (
              <MetricCard
                key={tier}
                label={tier}
                value={(n as number).toLocaleString()}
                tone={TIER_TONES[tier] ?? "neutral"}
              />
            ))}
          </div>

          <article className="rounded-lg border border-border-default bg-bg-canvas p-4">
            <h3 className="mb-3 text-sm font-medium text-fg-default">
              KTS 6-Signal 레이더
            </h3>
            <div style={{ width: "100%", height: 360 }}>
              <ResponsiveContainer>
                <RadarChart data={chartData} cx="50%" cy="50%" outerRadius="70%">
                  <PolarGrid stroke="var(--color-border-default)" />
                  <PolarAngleAxis
                    dataKey="signal"
                    tick={{
                      fill: "var(--color-fg-muted)",
                      fontSize: 11,
                    }}
                  />
                  <PolarRadiusAxis
                    angle={90}
                    domain={[0, 1]}
                    tick={{
                      fill: "var(--color-fg-subtle)",
                      fontSize: 9,
                    }}
                  />
                  <Tooltip
                    formatter={(v: unknown) =>
                      typeof v === "number"
                        ? [v.toFixed(3), "score"]
                        : [String(v), ""]
                    }
                    contentStyle={{
                      background: "var(--color-bg-canvas)",
                      border: "1px solid var(--color-border-default)",
                      borderRadius: 6,
                      fontSize: 12,
                    }}
                    labelStyle={{
                      color: "var(--color-fg-default)",
                    }}
                  />
                  <Radar
                    name="KTS"
                    dataKey="value"
                    stroke="var(--color-accent-default)"
                    fill="var(--color-accent-default)"
                    fillOpacity={0.35}
                    isAnimationActive={false}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </article>
        </>
      )}
    </div>
  );
}
