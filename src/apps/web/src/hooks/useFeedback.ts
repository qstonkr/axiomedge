"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listErrorReports,
  listFeedback,
  submitErrorReport,
  submitFeedback,
  type ErrorReportBody,
  type FeedbackBody,
} from "@/lib/api/endpoints";

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

export function useSubmitFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FeedbackBody) => submitFeedback(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["feedback", "list"] }),
  });
}

export function useSubmitErrorReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ErrorReportBody) => submitErrorReport(body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["error-reports", "list"] }),
  });
}
