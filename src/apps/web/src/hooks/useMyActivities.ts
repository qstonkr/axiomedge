"use client";

import { useQuery } from "@tanstack/react-query";

import {
  getMyActivities,
  getMyActivitySummary,
  type MyActivity,
  type MyActivitySummary,
} from "@/lib/api/endpoints";

/** 대시보드 4 메트릭 (총/기간/검색/피드백) — 기본 30일 윈도우. */
export function useMyActivitySummary(days = 30) {
  return useQuery<MyActivitySummary>({
    queryKey: ["my-activities", "summary", days],
    queryFn: () => getMyActivitySummary(days),
    staleTime: 60 * 1000,
  });
}

/** 타임라인 — type/날짜 필터. */
export function useMyActivities(params: {
  activity_type?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
}) {
  return useQuery<{ activities: MyActivity[]; total: number }>({
    queryKey: ["my-activities", "list", params],
    queryFn: () => getMyActivities(params),
    staleTime: 30 * 1000,
  });
}
