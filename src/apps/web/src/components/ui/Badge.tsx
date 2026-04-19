import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "./cn";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger";

const TONE: Record<Tone, string> = {
  neutral: "bg-bg-emphasis text-fg-default",
  accent: "bg-accent-subtle text-accent-emphasis",
  success: "bg-success-subtle text-success-default",
  warning: "bg-warning-subtle text-warning-default",
  danger: "bg-danger-subtle text-danger-default",
};

export type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  tone?: Tone;
  children: ReactNode;
};

export function Badge({ tone = "neutral", className, children, ...rest }: BadgeProps) {
  return (
    <span
      {...rest}
      className={cn(
        "inline-flex items-center rounded-pill px-2.5 py-0.5 text-xs font-medium",
        TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
