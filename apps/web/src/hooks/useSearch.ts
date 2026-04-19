"use client";

import { useMutation, useQuery } from "@tanstack/react-query";

import {
  agenticAsk,
  listKbs,
  searchHub,
  type AgenticAskRequest,
  type AgenticAskResponse,
  type HubSearchRequest,
  type HubSearchResponse,
  type Kb,
} from "@/lib/api/endpoints";

/** All active KBs the caller's org can see (sidebar / chat selector). */
export function useSearchableKbs() {
  return useQuery<Kb[]>({
    queryKey: ["kbs", "active"],
    queryFn: async () => {
      const { kbs } = await listKbs({ status: "active" });
      return kbs;
    },
    staleTime: 5 * 60 * 1000, // KB list rarely changes mid-session
  });
}

/** Fast hybrid search — chunks only, no LLM answer. */
export function useHubSearch() {
  return useMutation<HubSearchResponse, Error, HubSearchRequest>({
    mutationFn: (body) => searchHub(body),
  });
}

/** Agentic ask — plan → execute → synthesize → answer + sources. */
export function useAgenticAsk() {
  return useMutation<AgenticAskResponse, Error, AgenticAskRequest>({
    mutationFn: (body) => agenticAsk(body),
  });
}
