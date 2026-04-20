import { forwardRef, type TextareaHTMLAttributes } from "react";

import { cn } from "./cn";

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  invalid?: boolean;
};

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea({ invalid, className, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        {...rest}
        aria-invalid={invalid || undefined}
        className={cn(
          "block min-h-[112px] w-full rounded-md border bg-bg-canvas px-3 py-2 text-sm text-fg-default",
          "placeholder:text-fg-subtle disabled:cursor-not-allowed disabled:bg-bg-subtle disabled:text-fg-muted",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-default/40 focus-visible:ring-offset-1 focus-visible:ring-offset-bg-canvas",
          invalid
            ? "border-danger-default focus:border-danger-default"
            : "border-border-default focus:border-accent-default",
          className,
        )}
      />
    );
  },
);
