import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "./cn";

export type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  invalid?: boolean;
};

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      {...rest}
      aria-invalid={invalid || undefined}
      className={cn(
        "block h-9 w-full rounded-md border bg-bg-canvas px-3 text-sm text-fg-default",
        "placeholder:text-fg-subtle disabled:cursor-not-allowed disabled:bg-bg-subtle disabled:text-fg-muted",
        // focus border 만으로는 키보드 사용자가 인지 어려움 — focus-visible ring 추가.
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-default/40 focus-visible:ring-offset-1 focus-visible:ring-offset-bg-canvas",
        invalid
          ? "border-danger-default focus:border-danger-default"
          : "border-border-default focus:border-accent-default",
        className,
      )}
    />
  );
});
