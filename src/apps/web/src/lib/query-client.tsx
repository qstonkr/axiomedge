"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

/**
 * App-wide TanStack Query client.
 *
 * Defaults mirror the Streamlit cache semantics we're replacing:
 *   - search results: 60s staleTime
 *   - lookups (KB list etc.): 5 min staleTime
 *
 * Per-hook overrides win, so the defaults are deliberately conservative.
 */
export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60 * 1000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
