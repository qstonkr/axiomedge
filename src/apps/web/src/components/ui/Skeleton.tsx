import type { HTMLAttributes } from "react";

import { cn } from "./cn";

export function Skeleton({
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      {...rest}
      aria-hidden
      className={cn(
        "animate-pulse rounded-md bg-bg-muted",
        className,
      )}
    />
  );
}
