"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createDistillProfile,
  deleteBaseModel,
  deleteBuild,
  deleteDistillProfile,
  deleteEdgeServer,
  deployBuild,
  getEdgeManifest,
  getTrainingDataStats,
  listBaseModels,
  listDistillBuilds,
  listDistillProfiles,
  requestEdgeUpdate,
  rollbackBuild,
  triggerGenerateTrainingData,
  triggerRetrain,
  updateDistillProfile,
  upsertBaseModel,
  type BaseModel,
  type DistillBuild,
  type DistillProfile,
  type DistillProfileCreateBody,
  type DistillProfileUpdateBody,
  type EdgeManifest,
  type TrainingDataStats,
} from "@/lib/api/endpoints";

// ── profiles ──
export function useDistillProfiles() {
  return useQuery<DistillProfile[]>({
    queryKey: ["admin", "distill", "profiles"],
    queryFn: () => listDistillProfiles(),
    staleTime: 60 * 1000,
  });
}

export function useDeleteDistillProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteDistillProfile(name),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "distill", "profiles"] }),
  });
}

export function useCreateDistillProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DistillProfileCreateBody) => createDistillProfile(body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "distill", "profiles"] }),
  });
}

export function useUpdateDistillProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; body: DistillProfileUpdateBody }) =>
      updateDistillProfile(input.name, input.body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "distill", "profiles"] }),
  });
}

// ── base-models ──
export function useBaseModels() {
  return useQuery<BaseModel[]>({
    queryKey: ["admin", "distill", "base-models"],
    queryFn: () => listBaseModels(false),
    staleTime: 5 * 60 * 1000,
  });
}

export function useUpsertBaseModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BaseModel) => upsertBaseModel(body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "distill", "base-models"] }),
  });
}

export function useDeleteBaseModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (hfId: string) => deleteBaseModel(hfId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "distill", "base-models"] }),
  });
}

// ── builds (학습/배포) ──
export function useDistillBuilds() {
  return useQuery<DistillBuild[]>({
    queryKey: ["admin", "distill", "builds"],
    queryFn: () => listDistillBuilds(),
    staleTime: 30 * 1000,
    refetchInterval: 30 * 1000, // 학습 진행 상태 반영
  });
}

export function useTriggerRetrain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (profileName: string) =>
      triggerRetrain({ profile_name: profileName }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "distill", "builds"] }),
  });
}

export function useDeployBuild() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (buildId: string) => deployBuild(buildId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "distill", "builds"] }),
  });
}

export function useRollbackBuild() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (buildId: string) => rollbackBuild(buildId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "distill", "builds"] }),
  });
}

export function useDeleteBuild() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (buildId: string) => deleteBuild(buildId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "distill", "builds"] }),
  });
}

// ── training-data ──
export function useTrainingDataStats(profileName: string | null) {
  return useQuery<TrainingDataStats>({
    queryKey: ["admin", "distill", "training-data", profileName],
    queryFn: () => getTrainingDataStats(profileName!),
    enabled: Boolean(profileName),
    staleTime: 30 * 1000,
  });
}

export function useTriggerGenerateTrainingData() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { profile_name: string; num_samples?: number }) =>
      triggerGenerateTrainingData(input),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "distill", "training-data"] }),
  });
}

// ── edge-servers (delete + request-update) ──
export function useDeleteEdgeServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (storeId: string) => deleteEdgeServer(storeId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "edge"] }),
  });
}

export function useRequestEdgeUpdate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (storeId: string) => requestEdgeUpdate(storeId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "edge"] }),
  });
}

// ── manifest ──
export function useEdgeManifest(profileName: string | null) {
  return useQuery<EdgeManifest>({
    queryKey: ["admin", "distill", "manifest", profileName],
    queryFn: () => getEdgeManifest(profileName!),
    enabled: Boolean(profileName),
    staleTime: 60 * 1000,
  });
}
