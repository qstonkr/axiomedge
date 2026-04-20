"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveDiscoveredSynonyms,
  approveGlossaryTerm,
  createGlossaryTerm,
  createSearchGroup,
  deleteGlossaryTerm,
  deleteSearchGroup,
  getGlossarySimilarityDistribution,
  getPipelineStatus,
  listDedupConflicts,
  listDiscoveredSynonyms,
  listDocumentOwners,
  listGlossaryTerms,
  listPendingVerifications,
  listSearchGroups,
  rejectDiscoveredSynonyms,
  rejectGlossaryTerm,
  resolveDedupConflict,
  triggerIngestion,
  updateGlossaryTerm,
  updateSearchGroup,
  type DedupConflict,
  type GlossaryTerm,
  type GlossaryUpsertBody,
  type PipelineStatus,
  type SearchGroupUpsertBody,
  type SimilarityDistribution,
  type TriggerIngestionBody,
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

export function useTriggerIngestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TriggerIngestionBody) => triggerIngestion(body),
    onSuccess: () => {
      // 새 run 이 생기면 pipeline status / runs / job-monitor 모두 invalidate.
      qc.invalidateQueries({ queryKey: ["admin", "pipeline", "status"] });
      qc.invalidateQueries({ queryKey: ["admin", "ingest", "runs"] });
      qc.invalidateQueries({ queryKey: ["admin", "jobs"] });
    },
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
/** RapidFuzz 유사도 분포 — 글로서리 품질 모니터링 (장기 데이터, 5분 캐시). */
export function useGlossarySimilarityDistribution() {
  return useQuery<SimilarityDistribution>({
    queryKey: ["admin", "glossary", "similarity-distribution"],
    queryFn: () => getGlossarySimilarityDistribution(),
    staleTime: 5 * 60 * 1000,
  });
}

/** 자동 발견된 동의어 후보 큐 — 운영자 승인/거부 대상. */
export function useDiscoveredSynonyms(params?: {
  status?: "pending" | "approved" | "rejected";
  page?: number;
  page_size?: number;
}) {
  return useQuery<{ items: GlossaryTerm[]; total: number }>({
    queryKey: ["admin", "glossary", "discovered-synonyms", params],
    queryFn: () => listDiscoveredSynonyms(params),
    staleTime: 60 * 1000,
  });
}

export function useApproveDiscoveredSynonyms() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (synonymIds: string[]) => approveDiscoveredSynonyms(synonymIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "glossary"] });
    },
  });
}

export function useRejectDiscoveredSynonyms() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (synonymIds: string[]) => rejectDiscoveredSynonyms(synonymIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "glossary"] });
    },
  });
}

export function useDocumentOwners(kbId: string | null) {
  return useQuery({
    queryKey: ["admin", "owners", kbId],
    queryFn: () => listDocumentOwners({ kb_id: kbId! }),
    enabled: Boolean(kbId),
    staleTime: 60 * 1000,
  });
}

// ── /admin/glossary mutations ──
export function useCreateGlossaryTerm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GlossaryUpsertBody) => createGlossaryTerm(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "glossary"] }),
  });
}

export function useUpdateGlossaryTerm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; body: Partial<GlossaryUpsertBody> }) =>
      updateGlossaryTerm(input.id, input.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "glossary"] }),
  });
}

export function useDeleteGlossaryTerm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (termId: string) => deleteGlossaryTerm(termId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "glossary"] }),
  });
}

export function useApproveGlossaryTerm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (termId: string) => approveGlossaryTerm(termId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "glossary"] }),
  });
}

export function useRejectGlossaryTerm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; reason?: string }) =>
      rejectGlossaryTerm(input.id, input.reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "glossary"] }),
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

export function useCreateSearchGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SearchGroupUpsertBody) => createSearchGroup(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "search-groups"] }),
  });
}

export function useUpdateSearchGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; body: SearchGroupUpsertBody }) =>
      updateSearchGroup(input.id, input.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "search-groups"] }),
  });
}

export function useDeleteSearchGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (groupId: string) => deleteSearchGroup(groupId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "search-groups"] }),
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
