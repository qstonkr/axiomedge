"use client";

import type { ReactNode } from "react";

import { QueryProvider } from "@/lib/query-client";
import { ToastProvider } from "@/components/ui/Toast";

/** All client-side providers needed by the (app) tree. */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      <ToastProvider>{children}</ToastProvider>
    </QueryProvider>
  );
}
