"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getKbLifecycle,
  getPipelineGatesBlocked,
  getPipelineGatesStats,
  getTransparencyStats,
  submitVerificationVote,
  type BlockedDocument,
  type KbLifecycle,
  type PipelineGatesStats,
  type TransparencyStats,
} from "@/lib/api/endpoints";

export function useKbLifecycle(kbId: string | null) {
  return useQuery<KbLifecycle>({
    queryKey: ["admin", "lifecycle", kbId],
    queryFn: () => getKbLifecycle(kbId!),
    enabled: Boolean(kbId),
    staleTime: 60 * 1000,
  });
}

export function useGatesStats() {
  return useQuery<PipelineGatesStats>({
    queryKey: ["admin", "gates", "stats"],
    queryFn: () => getPipelineGatesStats(),
    staleTime: 30 * 1000,
  });
}

export function useGatesBlocked() {
  return useQuery<BlockedDocument[]>({
    queryKey: ["admin", "gates", "blocked"],
    queryFn: () => getPipelineGatesBlocked(),
    staleTime: 30 * 1000,
  });
}

export function useTransparencyStats() {
  return useQuery<TransparencyStats>({
    queryKey: ["admin", "transparency"],
    queryFn: () => getTransparencyStats(),
    staleTime: 5 * 60 * 1000,
  });
}

export function useVerificationVote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      docId: string;
      voteType: "upvote" | "downvote";
      kbId?: string;
    }) =>
      submitVerificationVote(input.docId, {
        vote_type: input.voteType,
        kb_id: input.kbId,
      }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "verifications"] }),
  });
}
