"use client";

import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export type HeroPoint = {
  /** x-axis label — "10:00", "Mon" 등 */
  t: string;
  v: number;
};

/**
 * Admin dashboard 의 큰 시계열 차트. recharts AreaChart wrapper.
 *
 * - 1 series, accent gradient fill
 * - hover tooltip 에 시각/값
 * - axis label 은 작게 (운영자 시각 weight 는 차트 자체)
 */
export function AreaChartHero({
  points,
  height = 160,
}: {
  points: HeroPoint[];
  height?: number;
}) {
  return (
    <div className="h-full w-full" style={{ minHeight: height }}>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart
          data={points}
          margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
        >
          <defs>
            <linearGradient id="hero-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-accent-default)" stopOpacity={0.45} />
              <stop offset="100%" stopColor="var(--color-accent-default)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="t"
            tick={{ fill: "var(--color-fg-subtle)", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
            minTickGap={24}
          />
          <YAxis
            width={40}
            tick={{ fill: "var(--color-fg-subtle)", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
            tickFormatter={(v: number) =>
              v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
            }
          />
          <Tooltip
            cursor={{
              stroke: "var(--color-accent-default)",
              strokeWidth: 1,
              strokeDasharray: "3 3",
            }}
            contentStyle={{
              background: "var(--color-bg-canvas)",
              border: "1px solid var(--color-border-default)",
              borderRadius: 6,
              fontSize: 12,
              padding: "6px 10px",
              color: "var(--color-fg-default)",
            }}
            labelStyle={{
              color: "var(--color-fg-muted)",
              fontSize: 10,
              marginBottom: 2,
            }}
            formatter={(v) => [Number(v).toLocaleString(), "건"]}
          />
          <Area
            type="monotone"
            dataKey="v"
            stroke="var(--color-accent-default)"
            strokeWidth={2}
            fill="url(#hero-fill)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
