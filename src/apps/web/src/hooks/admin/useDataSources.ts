"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listDataSources,
  triggerDataSourceSync,
  type DataSource,
} from "@/lib/api/endpoints";

const QK = ["admin", "data-sources"] as const;

export function useDataSources() {
  return useQuery<DataSource[]>({
    queryKey: [...QK, "list"],
    queryFn: () => listDataSources(),
    staleTime: 30 * 1000,
    refetchInterval: 30 * 1000, // sync 진행 상황 반영
  });
}

export function useTriggerDataSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) => triggerDataSourceSync(sourceId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...QK, "list"] });
    },
  });
}
