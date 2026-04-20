"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  assignAuthRole,
  cancelIngestRun,
  createAbacPolicy,
  createAuthUser,
  deleteAbacPolicy,
  deleteAuthUser,
  expandGraphNode,
  findGraphExperts,
  getConfigWeights,
  getGraphIntegrity,
  getGraphStats,
  getIngestRunDetail,
  getKbCategories,
  listAbacPolicies,
  listAuthRoles,
  listAuthUsers,
  listEdgeServers,
  listIngestRuns,
  listKbPermissions,
  listTopicOwners,
  resetConfigWeights,
  revokeAuthRole,
  revokeKbPermission,
  runGraphIntegrityCheck,
  searchGraphEntities,
  setKbPermission,
  updateAbacPolicy,
  updateAuthUser,
  updateConfigWeights,
  type AbacPolicy,
  type AbacPolicyUpsertBody,
  type AuthRole,
  type AuthUser,
  type AuthUserUpsertBody,
  type EdgeServer,
  type GraphIntegrity,
  type GraphStats,
  type KbCategory,
  type TopicOwner,
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

export function useSetKbPermission() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      kbId: string;
      body: { user_id: string; permission_level: KbPermission["permission_level"] };
    }) => setKbPermission(params.kbId, params.body),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["admin", "kb", vars.kbId, "permissions"] });
    },
  });
}

export function useRevokeKbPermission() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: { kbId: string; userId: string }) =>
      revokeKbPermission(params.kbId, params.userId),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["admin", "kb", vars.kbId, "permissions"] });
    },
  });
}

export function useCreateAbacPolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AbacPolicyUpsertBody) => createAbacPolicy(body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "abac", "policies"] }),
  });
}

export function useUpdateAbacPolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      policyId: string;
      body: Partial<AbacPolicyUpsertBody>;
    }) => updateAbacPolicy(params.policyId, params.body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "abac", "policies"] }),
  });
}

export function useDeleteAbacPolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (policyId: string) => deleteAbacPolicy(policyId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "abac", "policies"] }),
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

export function useUpdateConfigWeights() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => updateConfigWeights(body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "config", "weights"] }),
  });
}

export function useResetConfigWeights() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => resetConfigWeights(),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "config", "weights"] }),
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

/** topic 으로 expert 검색 — 비어있으면 query 비활성화. */
export function useGraphExperts(topic: string, limit = 10) {
  return useQuery({
    queryKey: ["admin", "graph", "experts", topic, limit],
    queryFn: () => findGraphExperts(topic, limit),
    enabled: topic.trim().length > 0,
    staleTime: 60 * 1000,
  });
}

/** 그래프 무결성 보고 (저장된 최근 결과 — 실시간 점검은 mutation). */
export function useGraphIntegrity() {
  return useQuery<GraphIntegrity>({
    queryKey: ["admin", "graph", "integrity"],
    queryFn: () => getGraphIntegrity(),
    staleTime: 60 * 1000,
  });
}

export function useRunGraphIntegrityCheck() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (kbId?: string) => runGraphIntegrityCheck(kbId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["admin", "graph", "integrity"] }),
  });
}

/** KB 의 L1 카테고리 분포 — dashboard L1 차트용. */
export function useKbCategories(kbId: string | null) {
  return useQuery<{ categories: KbCategory[]; total: number; kb_id: string }>({
    queryKey: ["admin", "kb", kbId, "categories"],
    queryFn: () => getKbCategories(kbId!),
    enabled: Boolean(kbId),
    staleTime: 5 * 60 * 1000,
  });
}

/** 한 ingestion run 의 상세 (status_logs 포함). expander 가 호출. */
export function useIngestRunDetail(runId: string | null) {
  return useQuery({
    queryKey: ["admin", "jobs", "detail", runId],
    queryFn: () => getIngestRunDetail(runId!),
    enabled: Boolean(runId),
    staleTime: 30 * 1000,
  });
}

/** Topic owner (전문가/SME) — KB scope. */
export function useTopicOwners(kbId: string | null) {
  return useQuery<{ topics: TopicOwner[]; total: number; kb_id: string }>({
    queryKey: ["admin", "ownership", "topics", kbId],
    queryFn: () => listTopicOwners(kbId!),
    enabled: Boolean(kbId),
    staleTime: 60 * 1000,
  });
}
