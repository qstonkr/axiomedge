import { forwardRef, type SelectHTMLAttributes } from "react";

import { cn } from "./cn";

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, children, ...rest },
  ref,
) {
  return (
    <select
      ref={ref}
      {...rest}
      className={cn(
        "block h-9 w-full rounded-md border border-border-default bg-bg-canvas px-3 text-sm text-fg-default",
        "focus:border-accent-default focus:outline-none",
        "disabled:cursor-not-allowed disabled:bg-bg-subtle disabled:text-fg-muted",
        className,
      )}
    >
      {children}
    </select>
  );
});
