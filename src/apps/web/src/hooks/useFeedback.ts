"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminCreateErrorReport,
  listErrorReports,
  listFeedback,
  listMyErrorReports,
  listMyFeedback,
  resolveErrorReport,
  submitErrorReport,
  submitFeedback,
  type ErrorReportBody,
  type FeedbackBody,
} from "@/lib/api/endpoints";

/**
 * Admin-scope — `/admin/errors` 등 운영자 화면 전용. 일반 사용자가 호출하면
 * 401/403. 사용자 본인 것만 보려면 ``useMyFeedbackList`` 를 사용할 것.
 */
export function useFeedbackList(params: {
  status?: string;
  feedback_type?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery({
    queryKey: ["feedback", "list", params],
    queryFn: () => listFeedback(params),
    staleTime: 30 * 1000,
  });
}

export function useErrorReportsList(params: {
  status?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery({
    queryKey: ["error-reports", "list", params],
    queryFn: () => listErrorReports(params),
    staleTime: 30 * 1000,
  });
}

/** User-scope — 본인이 제출한 피드백/오류 신고만. MEMBER/VIEWER 호출 가능. */
export function useMyFeedbackList(params?: {
  status?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery({
    queryKey: ["my-feedback", "list", params],
    queryFn: () => listMyFeedback(params),
    staleTime: 30 * 1000,
  });
}

export function useMyErrorReportsList(params?: {
  status?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery({
    queryKey: ["my-error-reports", "list", params],
    queryFn: () => listMyErrorReports(params),
    staleTime: 30 * 1000,
  });
}

export function useSubmitFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FeedbackBody) => submitFeedback(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["feedback", "list"] });
      qc.invalidateQueries({ queryKey: ["my-feedback", "list"] });
    },
  });
}

export function useSubmitErrorReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ErrorReportBody) => submitErrorReport(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["error-reports", "list"] });
      qc.invalidateQueries({ queryKey: ["my-error-reports", "list"] });
    },
  });
}

/** 운영자 직접 신고 — admin 화면 의 신규 신고 form 이 호출. */
export function useAdminCreateErrorReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof adminCreateErrorReport>[0]) =>
      adminCreateErrorReport(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["error-reports", "list"] });
      qc.invalidateQueries({ queryKey: ["my-error-reports", "list"] });
    },
  });
}

/** 신고 resolve — `/admin/errors` 화면이 호출. resolution_note 와 함께. */
export function useResolveErrorReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      reportId: string;
      body: { resolution_note?: string };
    }) => resolveErrorReport(params.reportId, params.body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["error-reports", "list"] }),
  });
}
