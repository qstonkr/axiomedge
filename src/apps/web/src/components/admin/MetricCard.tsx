import type { ReactNode } from "react";

import { cn } from "@/components/ui/cn";

import { Sparkline } from "./Sparkline";

export type MetricDelta = {
  value: number;
  label?: string; // "지난 7일", "어제 대비"
};

export function MetricCard({
  label,
  value,
  unit,
  delta,
  sparkline,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string | number | ReactNode;
  unit?: string;
  delta?: MetricDelta | null;
  sparkline?: number[];
  hint?: string;
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  const sparkColor =
    tone === "success"
      ? "text-success-default"
      : tone === "warning"
        ? "text-warning-default"
        : tone === "danger"
          ? "text-danger-default"
          : "text-accent-default";

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border-default bg-bg-canvas p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="text-xs font-medium text-fg-muted">{label}</div>
        {sparkline && sparkline.length > 1 && (
          <Sparkline points={sparkline} className={sparkColor} />
        )}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-3xl font-semibold tabular-nums text-fg-default">
          {value}
        </span>
        {unit && <span className="text-sm text-fg-muted">{unit}</span>}
      </div>
      {delta !== undefined && delta !== null && (
        <div className="flex items-center gap-1 text-xs">
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
            {unit ? "" : ""}
          </span>
          {delta.label && <span className="text-fg-subtle">{delta.label}</span>}
        </div>
      )}
      {hint && <p className="text-xs text-fg-subtle">{hint}</p>}
    </div>
  );
}
