"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { cn } from "./cn";

export type ToastType = "info" | "success" | "warning" | "danger";

type Toast = {
  id: string;
  message: string;
  type: ToastType;
};

type ToastContextValue = {
  push: (message: string, type?: ToastType) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

const STRIP: Record<ToastType, string> = {
  info: "bg-accent-default",
  success: "bg-success-default",
  warning: "bg-warning-default",
  danger: "bg-danger-default",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);

  const push = useCallback((message: string, type: ToastType = "info") => {
    const id = `toast-${++counter.current}`;
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3000);
  }, []);

  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div
        className="pointer-events-none fixed bottom-6 right-6 z-50 flex w-80 flex-col gap-2"
        aria-live="polite"
        aria-atomic="false"
      >
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast }: { toast: Toast }) {
  return (
    <div
      role="status"
      className={cn(
        "pointer-events-auto flex overflow-hidden rounded-lg border border-border-default bg-bg-canvas shadow-md",
      )}
    >
      <span aria-hidden className={cn("w-1 shrink-0", STRIP[toast.type])} />
      <span className="flex-1 px-4 py-3 text-sm text-fg-default">
        {toast.message}
      </span>
    </div>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within <ToastProvider>");
  }
  return ctx;
}

/** Headless helper — fires once on mount, useful for global error sinks. */
export function useToastOnMount(message: string | null, type: ToastType = "info") {
  const { push } = useToast();
  useEffect(() => {
    if (message) push(message, type);
  }, [message, type, push]);
}
