"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteGoldenItem,
  getAgentTrace,
  getDedupStats,
  getEvalStatus,
  listAgentTraces,
  listEvalHistory,
  listGoldenSet,
  triggerEval,
  type AgentTraceListItem,
  type DedupStats,
  type EvalRun,
  type EvalStatus,
  type GoldenItem,
} from "@/lib/api/endpoints";

// ── /admin/quality (RAG 메트릭 + dedup) ──
export function useDedupStats() {
  return useQuery<DedupStats>({
    queryKey: ["admin", "dedup", "stats"],
    queryFn: () => getDedupStats(),
    staleTime: 60 * 1000,
  });
}

export function useEvalStatus() {
  return useQuery<EvalStatus>({
    queryKey: ["admin", "eval", "status"],
    queryFn: () => getEvalStatus(),
    staleTime: 5 * 1000,
    refetchInterval: 5 * 1000,
  });
}

export function useEvalHistory(params?: { page?: number; page_size?: number }) {
  return useQuery<{ items: EvalRun[]; total: number }>({
    queryKey: ["admin", "eval", "history", params],
    queryFn: () => listEvalHistory(params),
    staleTime: 30 * 1000,
  });
}

export function useTriggerEval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: triggerEval,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "eval"] });
    },
  });
}

// ── /admin/golden-set ──
export function useGoldenSet(params?: {
  kb_id?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery<{ items: GoldenItem[]; total: number }>({
    queryKey: ["admin", "golden-set", params],
    queryFn: () => listGoldenSet(params),
    staleTime: 60 * 1000,
  });
}

export function useDeleteGoldenItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => deleteGoldenItem(itemId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "golden-set"] }),
  });
}

// ── /agentic/traces ──
export function useAgentTraces(limit = 50) {
  return useQuery<{ count: number; traces: AgentTraceListItem[] }>({
    queryKey: ["admin", "traces", limit],
    queryFn: () => listAgentTraces(limit),
    staleTime: 30 * 1000,
  });
}

export function useAgentTrace(traceId: string | null) {
  return useQuery({
    queryKey: ["admin", "trace", traceId],
    queryFn: () => getAgentTrace(traceId!),
    enabled: Boolean(traceId),
    staleTime: 5 * 60 * 1000, // trace 는 immutable
  });
}
