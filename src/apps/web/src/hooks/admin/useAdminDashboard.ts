"use client";

import { useQuery } from "@tanstack/react-query";

import {
  getAdminDashboardSummary,
  type AdminDashboardSummary,
} from "@/lib/api/endpoints";

/** 운영 대시보드 6 카운터 — 60초 stale, 운영자가 새로고침 빈도 높지 않음. */
export function useAdminDashboardSummary() {
  return useQuery<AdminDashboardSummary>({
    queryKey: ["admin", "dashboard", "summary"],
    queryFn: () => getAdminDashboardSummary(),
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000, // 1분마다 자동 갱신 (대시보드 = 항상 최신)
  });
}
