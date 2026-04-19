import { cn } from "@/components/ui/cn";

export type Severity =
  | "info"
  | "warn"
  | "error"
  | "critical"
  | "success"
  | "neutral";

const TONE: Record<Severity, string> = {
  info: "bg-[var(--color-severity-info)]/15 text-[var(--color-severity-info)] border-[var(--color-severity-info)]/30",
  warn: "bg-[var(--color-severity-warn)]/15 text-[var(--color-severity-warn)] border-[var(--color-severity-warn)]/30",
  error: "bg-[var(--color-severity-error)]/15 text-[var(--color-severity-error)] border-[var(--color-severity-error)]/30",
  critical: "bg-[var(--color-severity-critical)]/20 text-fg-onAccent border-[var(--color-severity-critical)]/40",
  success: "bg-[var(--color-severity-success)]/15 text-[var(--color-severity-success)] border-[var(--color-severity-success)]/30",
  neutral: "bg-bg-muted text-fg-muted border-border-default",
};

export function SeverityBadge({
  level,
  children,
  className,
}: {
  level: Severity;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        TONE[level],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** 자주 쓰는 status 문자열 → severity 매핑. */
export function statusToSeverity(status: string | null | undefined): Severity {
  const s = (status || "").toLowerCase();
  if (["healthy", "active", "connected", "completed", "success", "ok"].includes(s)) return "success";
  if (["warning", "warn", "queued", "pending"].includes(s)) return "warn";
  if (["error", "failed", "disconnected"].includes(s)) return "error";
  if (["critical", "down"].includes(s)) return "critical";
  if (["syncing", "running", "in_progress"].includes(s)) return "info";
  return "neutral";
}
