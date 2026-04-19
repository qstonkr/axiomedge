import type { ReactNode } from "react";

import { cn } from "./cn";

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-h-[200px] flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border-default bg-bg-subtle px-6 py-10 text-center",
        className,
      )}
    >
      {icon && (
        <div aria-hidden className="text-3xl text-fg-subtle">
          {icon}
        </div>
      )}
      <h3 className="text-base font-medium text-fg-default">{title}</h3>
      {description && (
        <p className="max-w-sm text-sm text-fg-muted">{description}</p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
