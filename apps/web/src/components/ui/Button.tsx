import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "./cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-accent-default text-fg-onAccent hover:bg-accent-emphasis disabled:opacity-50",
  secondary:
    "bg-bg-emphasis text-fg-default hover:bg-bg-muted disabled:opacity-50",
  ghost:
    "bg-transparent text-fg-default hover:bg-bg-muted disabled:opacity-50",
  danger:
    "bg-danger-default text-fg-onAccent hover:opacity-90 disabled:opacity-50",
};

const SIZE: Record<Size, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-9 px-4 text-sm",
  lg: "h-10 px-5 text-sm",
};

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
};

export function Button({
  variant = "primary",
  size = "md",
  leftIcon,
  rightIcon,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-default focus-visible:outline-offset-2",
        VARIANT[variant],
        SIZE[size],
        rest.disabled && "cursor-not-allowed",
        className,
      )}
    >
      {leftIcon}
      <span>{children}</span>
      {rightIcon}
    </button>
  );
}
