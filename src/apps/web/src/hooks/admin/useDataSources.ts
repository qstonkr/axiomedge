"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createDataSource,
  deleteDataSource,
  listDataSources,
  triggerDataSourceSync,
  updateDataSource,
  type DataSource,
  type DataSourceUpsertBody,
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

export function useCreateDataSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DataSourceUpsertBody) => createDataSource(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...QK, "list"] }),
  });
}

export function useUpdateDataSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; body: Partial<DataSourceUpsertBody> }) =>
      updateDataSource(input.id, input.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...QK, "list"] }),
  });
}

export function useDeleteDataSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) => deleteDataSource(sourceId),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...QK, "list"] }),
  });
}
