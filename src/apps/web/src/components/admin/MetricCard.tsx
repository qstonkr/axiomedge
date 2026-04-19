import type { ReactNode } from "react";

import { cn } from "@/components/ui/cn";

import { Sparkline } from "./Sparkline";

export type MetricDelta = {
  value: number;
  label?: string; // "지난 7일", "어제 대비"
};

const TONE_BAR: Record<MetricTone, string> = {
  neutral: "bg-accent-default",
  success: "bg-success-default",
  warning: "bg-warning-default",
  danger: "bg-danger-default",
};

const TONE_TEXT: Record<MetricTone, string> = {
  neutral: "text-accent-default",
  success: "text-success-default",
  warning: "text-warning-default",
  danger: "text-danger-default",
};

type MetricTone = "neutral" | "success" | "warning" | "danger";

export function MetricCard({
  label,
  value,
  unit,
  delta,
  sparkline,
  hint,
  tone = "neutral",
  icon,
}: {
  label: string;
  value: string | number | ReactNode;
  unit?: string;
  delta?: MetricDelta | null;
  /** 시계열 raw 값. 없으면 placeholder 패턴 표시. */
  sparkline?: number[];
  hint?: string;
  tone?: MetricTone;
  /** label 좌측 emoji/icon. */
  icon?: ReactNode;
}) {
  return (
    <article
      className={cn(
        "group relative flex flex-col gap-2 overflow-hidden rounded-lg border border-border-default bg-bg-canvas p-4",
        "transition-colors hover:border-border-strong",
      )}
    >
      {/* 좌측 accent strip — tone 으로 즉시 상태 인식 */}
      <span
        aria-hidden
        className={cn("absolute left-0 top-0 h-full w-0.5", TONE_BAR[tone])}
      />

      <header className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-1.5 text-xs font-medium text-fg-muted">
          {icon && (
            <span aria-hidden className="text-sm leading-none">
              {icon}
            </span>
          )}
          <span>{label}</span>
        </div>
        {sparkline && sparkline.length > 1 ? (
          <Sparkline points={sparkline} className={TONE_TEXT[tone]} />
        ) : (
          // 빈 sparkline 도 동일한 폭을 차지해 카드 높이가 점프하지 않도록.
          <span aria-hidden className="block h-7 w-[100px]" />
        )}
      </header>

      <div className="flex items-baseline gap-1.5">
        <span className="text-3xl font-semibold tabular-nums tracking-tight text-fg-default">
          {value}
        </span>
        {unit && <span className="text-sm text-fg-muted">{unit}</span>}
      </div>

      {(delta !== undefined && delta !== null) || hint ? (
        <footer className="flex items-center justify-between gap-2 text-xs">
          {delta !== undefined && delta !== null ? (
            <span className="flex items-center gap-1">
              <span
                className={cn(
                  "font-medium tabular-nums",
                  delta.value > 0 && "text-success-default",
                  delta.value < 0 && "text-danger-default",
                  delta.value === 0 && "text-fg-subtle",
                )}
              >
                {delta.value > 0 ? "▲" : delta.value < 0 ? "▼" : "−"}
                {Math.abs(delta.value).toLocaleString()}
              </span>
              {delta.label && (
                <span className="text-fg-subtle">{delta.label}</span>
              )}
            </span>
          ) : (
            <span />
          )}
          {hint && <span className="text-fg-subtle">{hint}</span>}
        </footer>
      ) : null}
    </article>
  );
}
