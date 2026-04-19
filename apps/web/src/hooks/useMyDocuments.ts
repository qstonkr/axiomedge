"use client";

import { useQuery } from "@tanstack/react-query";

import {
  getSearchHistory,
  listDocumentOwners,
  listErrorReports,
  listFeedback,
  listPendingVerifications,
  listStaleOwners,
  type DocumentOwner,
} from "@/lib/api/endpoints";

export function useMyDocumentOwners(params: { kb_id?: string; userId: string }) {
  return useQuery({
    queryKey: ["my-documents", "owners", params],
    queryFn: async () => {
      const all = await listDocumentOwners({ kb_id: params.kb_id });
      // The endpoint isn't user-scoped server-side yet; filter client-side
      // until the API ships an `owner_user_id` query parameter.
      return all.filter(
        (o) => o.owner_user_id === params.userId,
      ) as DocumentOwner[];
    },
    enabled: Boolean(params.userId),
    staleTime: 60 * 1000,
  });
}

/**
 * Composite "대기 작업" view — joins three lists into one badge-able set.
 * Each list is its own React Query so cache hits survive tab switches.
 */
export function usePendingTasks() {
  const verifications = useQuery({
    queryKey: ["my-documents", "pending", "verifications"],
    queryFn: () => listPendingVerifications(),
    staleTime: 60 * 1000,
  });
  const feedback = useQuery({
    queryKey: ["my-documents", "pending", "feedback"],
    queryFn: () => listFeedback({ status: "pending", page: 1, page_size: 50 }),
    staleTime: 60 * 1000,
  });
  const errors = useQuery({
    queryKey: ["my-documents", "pending", "errors"],
    queryFn: () => listErrorReports({ status: "pending", page: 1, page_size: 50 }),
    staleTime: 60 * 1000,
  });
  return {
    verifications: verifications.data ?? [],
    feedback: feedback.data?.items ?? [],
    errors: errors.data?.items ?? [],
    isLoading:
      verifications.isLoading || feedback.isLoading || errors.isLoading,
  };
}

export function useStaleOwners(params: { kb_id?: string }) {
  return useQuery({
    queryKey: ["my-documents", "stale", params.kb_id ?? null],
    queryFn: () => listStaleOwners({ kb_id: params.kb_id }),
    staleTime: 5 * 60 * 1000,
  });
}

export function useSearchHistory(params: { page: number; page_size: number }) {
  return useQuery({
    queryKey: ["search-history", params.page, params.page_size],
    queryFn: () => getSearchHistory(params),
    staleTime: 60 * 1000,
  });
}
