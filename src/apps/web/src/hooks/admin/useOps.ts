"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  assignAuthRole,
  cancelIngestRun,
  createAuthUser,
  deleteAuthUser,
  expandGraphNode,
  getConfigWeights,
  getGraphStats,
  listAbacPolicies,
  listAuthRoles,
  listAuthUsers,
  listEdgeServers,
  listIngestRuns,
  listKbPermissions,
  revokeAuthRole,
  searchGraphEntities,
  updateAuthUser,
  type AbacPolicy,
  type AuthRole,
  type AuthUser,
  type AuthUserUpsertBody,
  type EdgeServer,
  type GraphStats,
  type IngestRun,
  type KbPermission,
} from "@/lib/api/endpoints";

// ── /admin/users ──
export function useAuthUsers() {
  return useQuery<{ users: AuthUser[]; total?: number }>({
    queryKey: ["admin", "users"],
    queryFn: () => listAuthUsers({ limit: 200 }),
    staleTime: 60 * 1000,
  });
}

export function useAuthRoles() {
  return useQuery<AuthRole[]>({
    queryKey: ["admin", "roles"],
    queryFn: () => listAuthRoles(),
    staleTime: 5 * 60 * 1000,
  });
}

/** ABAC 정책 list (admin/system 권한 필요). */
export function useAbacPolicies() {
  return useQuery<AbacPolicy[]>({
    queryKey: ["admin", "abac", "policies"],
    queryFn: () => listAbacPolicies(),
    staleTime: 60 * 1000,
  });
}

/** 한 KB 의 사용자 권한 목록. kbId 가 null/빈문자열이면 query 비활성화. */
export function useKbPermissions(kbId: string | null) {
  return useQuery<{ kb_id: string; permissions: KbPermission[] }>({
    queryKey: ["admin", "kb", kbId, "permissions"],
    queryFn: () => listKbPermissions(kbId!),
    enabled: Boolean(kbId),
    staleTime: 60 * 1000,
  });
}

export function useCreateAuthUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AuthUserUpsertBody) => createAuthUser(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useUpdateAuthUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; body: Partial<AuthUserUpsertBody> }) =>
      updateAuthUser(input.id, input.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useDeleteAuthUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => deleteAuthUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useAssignAuthRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { userId: string; role: string }) =>
      assignAuthRole(input.userId, input.role),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useRevokeAuthRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { userId: string; role: string }) =>
      revokeAuthRole(input.userId, input.role),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

// ── /admin/edge ──
export function useEdgeServers() {
  return useQuery<EdgeServer[]>({
    queryKey: ["admin", "edge"],
    queryFn: () => listEdgeServers(),
    staleTime: 30 * 1000,
    refetchInterval: 30 * 1000, // heartbeat 변화 반영
  });
}

// ── /admin/jobs ──
/**
 * autoRefresh=true (기본) 면 15초마다 polling. UI 에서 사용자가 toggle
 * 끄면 false 로 전달 — useQuery 의 refetchInterval 이 false 가 되어 polling
 * 중단. 페이지 백그라운드 탭일 때 (focus 잃었을 때) 도 자동 중단됨 (TanStack
 * default refetchIntervalInBackground=false).
 */
export function useIngestRuns(autoRefresh = true, intervalMs = 15_000) {
  return useQuery<IngestRun[]>({
    queryKey: ["admin", "jobs"],
    queryFn: () => listIngestRuns(),
    staleTime: 15 * 1000,
    refetchInterval: autoRefresh ? intervalMs : false,
  });
}

export function useCancelIngestRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => cancelIngestRun(runId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "jobs"] }),
  });
}

// ── /admin/config ──
export function useConfigWeights() {
  return useQuery<Record<string, unknown>>({
    queryKey: ["admin", "config", "weights"],
    queryFn: () => getConfigWeights(),
    staleTime: 5 * 60 * 1000,
    retry: 0, // backend 가 자주 500 — 재시도 시간 낭비
  });
}

// ── /admin/graph ──
export function useGraphStats() {
  return useQuery<GraphStats>({
    queryKey: ["admin", "graph", "stats"],
    queryFn: () => getGraphStats(),
    staleTime: 5 * 60 * 1000,
  });
}

export function useGraphSearch(body: {
  query: string;
  entity_types?: string[];
}) {
  return useQuery({
    queryKey: ["admin", "graph", "search", body],
    queryFn: () => searchGraphEntities(body),
    enabled: body.query.length > 0,
    staleTime: 60 * 1000,
  });
}

/** 한 노드의 1-hop 이웃 + edge 들 — 시각화용. node 클릭으로 활성화. */
export function useGraphExpand(nodeId: string | null, maxNeighbors = 24) {
  return useQuery({
    queryKey: ["admin", "graph", "expand", nodeId, maxNeighbors],
    queryFn: () => expandGraphNode(nodeId!, maxNeighbors),
    enabled: Boolean(nodeId),
    staleTime: 5 * 60 * 1000,
  });
}
