import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "./cn";

type Padding = "none" | "compact" | "default";

const PADDING: Record<Padding, string> = {
  none: "",
  compact: "p-4",
  default: "p-6",
};

export type CardProps = HTMLAttributes<HTMLDivElement> & {
  padding?: Padding;
  hoverable?: boolean;
};

export function Card({
  padding = "default",
  hoverable,
  className,
  children,
  ...rest
}: CardProps) {
  return (
    <div
      {...rest}
      className={cn(
        "rounded-lg border border-border-default bg-bg-canvas shadow-sm",
        hoverable && "transition-shadow hover:shadow-md",
        PADDING[padding],
        className,
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-3 space-y-1", className)}>{children}</div>
  );
}

export function CardTitle({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <h3 className={cn("text-lg font-medium leading-snug text-fg-default", className)}>
      {children}
    </h3>
  );
}

export function CardBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("text-sm leading-6 text-fg-muted", className)}>
      {children}
    </div>
  );
}
