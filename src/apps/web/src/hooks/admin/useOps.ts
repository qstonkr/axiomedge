"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelIngestRun,
  getConfigWeights,
  getGraphStats,
  listAuthRoles,
  listAuthUsers,
  listEdgeServers,
  listIngestRuns,
  searchGraphEntities,
  type AuthRole,
  type AuthUser,
  type EdgeServer,
  type GraphStats,
  type IngestRun,
} from "@/lib/api/endpoints";

// ── /admin/users ──
export function useAuthUsers() {
  return useQuery<{ users: AuthUser[]; total?: number }>({
    queryKey: ["admin", "users"],
    queryFn: () => listAuthUsers({ limit: 200 }),
    staleTime: 60 * 1000,
  });
}

export function useAuthRoles() {
  return useQuery<AuthRole[]>({
    queryKey: ["admin", "roles"],
    queryFn: () => listAuthRoles(),
    staleTime: 5 * 60 * 1000,
  });
}

// ── /admin/edge ──
export function useEdgeServers() {
  return useQuery<EdgeServer[]>({
    queryKey: ["admin", "edge"],
    queryFn: () => listEdgeServers(),
    staleTime: 30 * 1000,
    refetchInterval: 30 * 1000, // heartbeat 변화 반영
  });
}

// ── /admin/jobs ──
export function useIngestRuns() {
  return useQuery<IngestRun[]>({
    queryKey: ["admin", "jobs"],
    queryFn: () => listIngestRuns(),
    staleTime: 15 * 1000,
    refetchInterval: 15 * 1000,
  });
}

export function useCancelIngestRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => cancelIngestRun(runId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "jobs"] }),
  });
}

// ── /admin/config ──
export function useConfigWeights() {
  return useQuery<Record<string, unknown>>({
    queryKey: ["admin", "config", "weights"],
    queryFn: () => getConfigWeights(),
    staleTime: 5 * 60 * 1000,
    retry: 0, // backend 가 자주 500 — 재시도 시간 낭비
  });
}

// ── /admin/graph ──
export function useGraphStats() {
  return useQuery<GraphStats>({
    queryKey: ["admin", "graph", "stats"],
    queryFn: () => getGraphStats(),
    staleTime: 5 * 60 * 1000,
  });
}

export function useGraphSearch(body: {
  query: string;
  entity_types?: string[];
}) {
  return useQuery({
    queryKey: ["admin", "graph", "search", body],
    queryFn: () => searchGraphEntities(body),
    enabled: body.query.length > 0,
    staleTime: 60 * 1000,
  });
}
