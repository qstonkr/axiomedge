"use client";

import { useMutation, useQuery } from "@tanstack/react-query";

import {
  agenticAsk,
  getPopularQueries,
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

/** 추천 검색어 — 최근 검색 통계 기반 (없으면 fallback=true). */
export function usePopularQueries(days = 7, limit = 4) {
  return useQuery({
    queryKey: ["popular-queries", days, limit],
    queryFn: () => getPopularQueries(days, limit),
    staleTime: 10 * 60 * 1000, // 10분 — 추천이라 fresh 강제 안 함
  });
}
