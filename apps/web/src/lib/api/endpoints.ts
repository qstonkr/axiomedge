/**
 * Thin wrappers around the BFF proxy. Each function corresponds to one
 * FastAPI endpoint the user-facing pages need (Day 5–8). Heavy lifting
 * (cookie handling, refresh, error mapping) lives in ``client.ts``.
 *
 * Types are imported from the openapi-generated ``./types`` so signatures
 * stay in sync with FastAPI; falling back to ``Record`` only for the
 * endpoints that aren't fully described upstream.
 */
import { request } from "./client";

// ── /search/hub ─────────────────────────────────────────────────────────

export type HubSearchRequest = {
  query: string;
  kb_ids?: string[] | null;
  group_id?: string | null;
  group_name?: string | null;
  document_filter?: string[] | null;
  top_k?: number;
  include_answer?: boolean;
  mode?: string | null;
};

export type HubSearchResponse = {
  query: string;
  answer?: string | null;
  chunks: Array<Record<string, unknown>>;
  searched_kbs: string[];
  total_chunks: number;
  search_time_ms: number;
  query_type?: string;
  confidence?: string;
  metadata?: Record<string, unknown>;
  display_query?: string | null;
};

export const searchHub = (body: HubSearchRequest) =>
  request<HubSearchResponse>("api/v1/search/hub", {
    method: "POST",
    body: JSON.stringify(body),
  });

// ── /agentic/ask ────────────────────────────────────────────────────────

export type AgenticAskRequest = {
  query: string;
  kb_ids?: string[] | null;
};

export type AgenticAskResponse = {
  trace_id: string;
  answer: string;
  llm_provider: string;
  iteration_count: number;
  total_steps_executed: number;
  total_duration_ms: number;
  estimated_cost_usd: number;
  confidence: number;
};

export const agenticAsk = (body: AgenticAskRequest) =>
  request<AgenticAskResponse>("api/v1/agentic/ask", {
    method: "POST",
    body: JSON.stringify(body),
  });

// ── /admin/kb ───────────────────────────────────────────────────────────

export type Kb = {
  kb_id: string;
  id?: string;
  name: string;
  description?: string;
  tier: string;
  organization_id?: string | null;
  owner_id?: string | null;
  status?: string;
  document_count?: number;
  chunk_count?: number;
};

export const listKbs = (params?: { tier?: string; status?: string }) =>
  request<{ kbs: Kb[] }>("api/v1/admin/kb", {
    method: "GET",
    query: params,
  });

export const getKb = (kbId: string) =>
  request<Kb>(`api/v1/admin/kb/${encodeURIComponent(kbId)}`, { method: "GET" });

// Personal KB (B-1 Day 1) — tier hard-locked to "personal" upstream.
export const createPersonalKb = (body: {
  kb_id: string;
  name: string;
  description?: string;
}) =>
  request<{
    success: boolean;
    kb_id: string;
    tier: string;
    owner_id: string;
    message: string;
  }>("api/v1/kb/create", {
    method: "POST",
    body: JSON.stringify({ ...body, tier: "personal" }),
  });

export const deleteKb = (kbId: string) =>
  request<{ success: boolean }>(`api/v1/kb/${encodeURIComponent(kbId)}`, {
    method: "DELETE",
  });

// ── /search-groups ──────────────────────────────────────────────────────

export type SearchGroup = {
  id: string;
  name: string;
  kb_ids: string[];
  description?: string;
};

export const listSearchGroups = () =>
  request<SearchGroup[]>("api/v1/search-groups", { method: "GET" });

// ── /knowledge/feedback + /knowledge/report-error ───────────────────────

export type FeedbackBody = {
  feedback_type: "UPVOTE" | "DOWNVOTE" | "CORRECTION" | "ERROR_REPORT" | "SUGGESTION";
  document_id?: string | null;
  content: string;
};

export const submitFeedback = (body: FeedbackBody) =>
  request<{ id: string; success: boolean }>("api/v1/knowledge/feedback", {
    method: "POST",
    body: JSON.stringify(body),
  });

export type ErrorReportBody = {
  error_type:
    | "INACCURATE"
    | "OUTDATED"
    | "INCOMPLETE"
    | "DUPLICATE"
    | "BROKEN_LINK"
    | "FORMATTING"
    | "OTHER";
  priority: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  title: string;
  description: string;
  document_id?: string | null;
};

export const submitErrorReport = (body: ErrorReportBody) =>
  request<{ id: string; success: boolean }>("api/v1/knowledge/report-error", {
    method: "POST",
    body: JSON.stringify(body),
  });

// ── /admin/feedback + /admin/error-reports (목록 조회) ──────────────────

export const listFeedback = (params?: {
  status?: string;
  feedback_type?: string;
  page?: number;
  page_size?: number;
}) =>
  request<{ items: Array<Record<string, unknown>>; total: number }>(
    "api/v1/admin/feedback/list",
    { method: "GET", query: params },
  );

export const listErrorReports = (params?: {
  status?: string;
  page?: number;
  page_size?: number;
}) =>
  request<{ items: Array<Record<string, unknown>>; total: number }>(
    "api/v1/admin/error-reports",
    { method: "GET", query: params },
  );

// ── /admin/ownership ───────────────────────────────────────────────────

export type DocumentOwner = {
  id: string;
  document_id: string;
  document_title?: string;
  owner_user_id: string;
  ownership_type?: string;
  status?: string;
  assigned_at?: string;
  last_verified_at?: string | null;
  kb_id?: string;
};

export const listDocumentOwners = (params?: { kb_id?: string }) =>
  request<DocumentOwner[]>("api/v1/admin/ownership/documents", {
    method: "GET",
    query: params,
  });

export const listStaleOwners = (params?: { kb_id?: string }) =>
  request<DocumentOwner[]>("api/v1/admin/ownership/stale", {
    method: "GET",
    query: params,
  });

// ── /knowledge/experts/search ──────────────────────────────────────────

export type Owner = {
  id: string;
  name: string;
  team?: string;
  expertise?: string[];
  trust_score?: number;
  contact?: string;
  documents?: Array<Record<string, unknown>>;
};

export const searchOwners = (params: { query: string; kb_id?: string }) =>
  request<{ owners: Owner[] }>("api/v1/knowledge/experts/search", {
    method: "GET",
    query: params,
  });

// ── /admin/verification/pending ────────────────────────────────────────

export const listPendingVerifications = () =>
  request<Array<Record<string, unknown>>>("api/v1/admin/verification/pending", {
    method: "GET",
  });

// ── /admin/search/history ──────────────────────────────────────────────

export type SearchHistoryItem = {
  timestamp: string;
  query: string;
  result_count?: number;
  kb_ids?: string[];
  user_id?: string;
  response_time_ms?: number;
  source?: string;
};

export const getSearchHistory = (params?: { page?: number; page_size?: number }) =>
  request<{ items: SearchHistoryItem[]; total: number }>(
    "api/v1/admin/search/history",
    { method: "GET", query: params },
  );

// ── /knowledge/upload (document upload) ────────────────────────────────

export const uploadDocumentToKb = async (kbId: string, file: File) => {
  const form = new FormData();
  form.append("kb_id", kbId);
  form.append("file", file);
  // FormData omits Content-Type so the browser sets multipart boundary.
  return request<{ success: boolean; document_id?: string }>(
    "api/v1/knowledge/upload",
    {
      method: "POST",
      body: form as unknown as BodyInit,
      headers: {},
    },
  );
};
