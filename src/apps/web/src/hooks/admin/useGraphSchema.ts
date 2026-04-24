"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveGraphSchemaCandidate,
  listGraphSchemaCandidates,
  mergeGraphSchemaCandidate,
  rejectGraphSchemaCandidate,
  renameGraphSchemaCandidate,
  triggerGraphSchemaBootstrap,
  triggerGraphSchemaReextract,
  type GraphSchemaCandidate,
} from "@/lib/api/endpoints";

export function useGraphSchemaCandidates(kb_id: string) {
  return useQuery<{ candidates: GraphSchemaCandidate[] }>({
    queryKey: ["admin", "graph-schema", "candidates", kb_id],
    queryFn: () => listGraphSchemaCandidates(kb_id),
    enabled: Boolean(kb_id),
    staleTime: 30 * 1000,
  });
}

function useDecideMutation<TBody>(
  fn: (body: TBody) => Promise<unknown>,
  kb_id: string,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["admin", "graph-schema", "candidates", kb_id],
      });
    },
  });
}

export function useApproveCandidate(kb_id: string) {
  return useDecideMutation(approveGraphSchemaCandidate, kb_id);
}
export function useRejectCandidate(kb_id: string) {
  return useDecideMutation(rejectGraphSchemaCandidate, kb_id);
}
export function useMergeCandidate(kb_id: string) {
  return useDecideMutation(mergeGraphSchemaCandidate, kb_id);
}
export function useRenameCandidate(kb_id: string) {
  return useDecideMutation(renameGraphSchemaCandidate, kb_id);
}

export function useTriggerBootstrap() {
  return useMutation({
    mutationFn: (kb_id: string) => triggerGraphSchemaBootstrap(kb_id),
  });
}

export function useTriggerReextract() {
  return useMutation({
    mutationFn: ({ kb_id, triggered_by_user }: {
      kb_id: string; triggered_by_user: string;
    }) => triggerGraphSchemaReextract(kb_id, { triggered_by_user }),
  });
}
