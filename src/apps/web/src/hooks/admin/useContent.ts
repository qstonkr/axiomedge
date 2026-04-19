"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getPipelineStatus,
  listDedupConflicts,
  listDocumentOwners,
  listGlossaryTerms,
  listPendingVerifications,
  listSearchGroups,
  resolveDedupConflict,
  type DedupConflict,
  type GlossaryTerm,
  type PipelineStatus,
} from "@/lib/api/endpoints";

// ── /admin/ingest ──
export function usePipelineStatus() {
  return useQuery<PipelineStatus>({
    queryKey: ["admin", "pipeline", "status"],
    queryFn: () => getPipelineStatus(),
    staleTime: 15 * 1000,
    refetchInterval: 15 * 1000,
  });
}

// ── /admin/glossary ──
export function useGlossary(params?: {
  kb_id?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery<{ items: GlossaryTerm[]; total: number }>({
    queryKey: ["admin", "glossary", params],
    queryFn: () => listGlossaryTerms(params),
    staleTime: 60 * 1000,
  });
}

// ── /admin/owners ──
export function useDocumentOwners(kbId: string | null) {
  return useQuery({
    queryKey: ["admin", "owners", kbId],
    queryFn: () => listDocumentOwners({ kb_id: kbId! }),
    enabled: Boolean(kbId),
    staleTime: 60 * 1000,
  });
}

// ── /admin/groups ──
export function useSearchGroups() {
  return useQuery({
    queryKey: ["admin", "search-groups"],
    queryFn: () => listSearchGroups(),
    staleTime: 5 * 60 * 1000,
  });
}

// ── /admin/conflicts ──
export function useDedupConflicts(params?: {
  status?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery<{ items: DedupConflict[]; total: number }>({
    queryKey: ["admin", "conflicts", params],
    queryFn: () => listDedupConflicts(params),
    staleTime: 60 * 1000,
  });
}

export function useResolveDedupConflict() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: resolveDedupConflict,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "conflicts"] }),
  });
}

// ── /admin/verification ──
export function usePendingVerifications() {
  return useQuery({
    queryKey: ["admin", "verifications"],
    queryFn: () => listPendingVerifications(),
    staleTime: 60 * 1000,
  });
}
