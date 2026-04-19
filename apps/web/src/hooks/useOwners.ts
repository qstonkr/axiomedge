"use client";

import { useQuery } from "@tanstack/react-query";

import { searchOwners, type Owner } from "@/lib/api/endpoints";

export function useOwnerSearch(params: { query: string; kb_id?: string }) {
  const query = params.query.trim();
  return useQuery<{ owners: Owner[] }>({
    queryKey: ["owners", "search", query, params.kb_id ?? null],
    queryFn: () => searchOwners({ query, kb_id: params.kb_id }),
    // The user has to type something before we hit the backend.
    enabled: query.length > 0,
    staleTime: 60 * 1000,
  });
}
