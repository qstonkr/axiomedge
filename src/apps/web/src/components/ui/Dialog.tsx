"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { cn } from "./cn";

/**
 * Lightweight modal dialog — no heavy deps.
 *
 * - <body> 의 click outside / Escape 로 닫기
 * - 첫 progressive focus 는 dialog 안의 처음 [autofocus] 또는 첫 input 에 자동
 * - 열릴 때 body scroll lock (간단히 overflow hidden 토글)
 * - role="dialog" + aria-modal 으로 SR 사용자 인식
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: "sm" | "md" | "lg" | "xl";
}) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    // body scroll lock
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);

    // autofocus first focusable
    const first = dialogRef.current?.querySelector<HTMLElement>(
      "[autofocus], input, textarea, select, button:not([data-close])",
    );
    first?.focus?.();

    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  const widthClass =
    width === "sm"
      ? "max-w-sm"
      : width === "lg"
        ? "max-w-2xl"
        : width === "xl"
          ? "max-w-4xl"
          : "max-w-md";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      {/* backdrop */}
      <button
        type="button"
        aria-label="닫기"
        data-close
        onClick={onClose}
        className="absolute inset-0 bg-fg-default/40 backdrop-blur-sm"
      />
      {/* panel */}
      <div
        ref={dialogRef}
        className={cn(
          "relative z-10 flex max-h-[90vh] w-full flex-col rounded-lg border border-border-default bg-bg-canvas shadow-lg",
          widthClass,
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border-default px-5 py-3">
          <div className="flex flex-col gap-0.5">
            <h2 id="dialog-title" className="text-base font-medium text-fg-default">
              {title}
            </h2>
            {description && (
              <p className="text-xs text-fg-muted">{description}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            data-close
            aria-label="닫기"
            className="rounded-md p-1 text-fg-muted transition-colors hover:bg-bg-muted hover:text-fg-default"
          >
            ✕
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <footer className="flex items-center justify-end gap-2 border-t border-border-default bg-bg-subtle px-5 py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}
