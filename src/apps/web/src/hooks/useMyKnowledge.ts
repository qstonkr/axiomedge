"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createPersonalKb,
  deleteKb,
  listKbs,
  uploadDocumentToKb,
  type Kb,
} from "@/lib/api/endpoints";

const QK = ["my-knowledge", "list"] as const;

/**
 * Personal KBs the user owns. The /admin/kb endpoint is org-scoped, so we
 * client-side filter to ``tier === "personal" && owner_id === userId``.
 * Cheap — typical user owns ≤10 personal KBs.
 */
export function useMyPersonalKbs(userId: string) {
  return useQuery<Kb[]>({
    queryKey: [...QK, userId],
    queryFn: async () => {
      const { kbs } = await listKbs();
      return kbs.filter(
        (kb) => kb.tier === "personal" && kb.owner_id === userId,
      );
    },
    enabled: Boolean(userId),
    staleTime: 30 * 1000,
  });
}

export function useCreatePersonalKb(userId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createPersonalKb,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...QK, userId] });
    },
  });
}

export function useDeleteKb(userId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (kbId: string) => deleteKb(kbId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...QK, userId] });
    },
  });
}

export function useUploadDocument(kbId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadDocumentToKb(kbId, file),
    onSuccess: () => {
      // doc_count etc. lives on the KB row — refetch the list so the count
      // updates without a full page refresh.
      qc.invalidateQueries({ queryKey: QK });
    },
  });
}
