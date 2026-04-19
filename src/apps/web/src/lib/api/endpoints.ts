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
  failure_reason?: string | null;
  errors?: string[];
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

// Frontend 의 uppercase enum → backend ``FeedbackType`` (lowercase) 매핑.
// ``ERROR_REPORT`` 는 backend 에 별도 enum 이 없어서 ``report`` 로 변환.
const FEEDBACK_TYPE_MAP: Record<FeedbackBody["feedback_type"], string> = {
  UPVOTE: "upvote",
  DOWNVOTE: "downvote",
  CORRECTION: "correction",
  SUGGESTION: "suggestion",
  ERROR_REPORT: "report",
};

export const submitFeedback = (body: FeedbackBody) => {
  // backend 는 ``document_id`` 가 NULL 이면 500 — 빈 값/누락이면 필드 자체 omit
  // 해서 default ('unknown') 가 적용되게 한다.
  const payload: Record<string, unknown> = {
    feedback_type: FEEDBACK_TYPE_MAP[body.feedback_type],
    content: body.content,
  };
  const docId = body.document_id?.trim();
  if (docId) payload.document_id = docId;
  return request<{ id: string; success: boolean }>(
    "api/v1/knowledge/feedback",
    { method: "POST", body: JSON.stringify(payload) },
  );
};

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

export const submitErrorReport = (body: ErrorReportBody) => {
  // backend 는 error_type/priority 를 string 그대로 저장 (enum 검증 없음).
  // 다만 lowercase 컨벤션을 따라 보내서 admin UI 와 일관되게 한다. document_id
  // null 은 feedback 과 동일 사유로 omit.
  const payload: Record<string, unknown> = {
    error_type: body.error_type.toLowerCase(),
    priority: body.priority.toLowerCase(),
    title: body.title,
    description: body.description,
  };
  const docId = body.document_id?.trim();
  if (docId) payload.document_id = docId;
  return request<{ id: string; success: boolean }>(
    "api/v1/knowledge/report-error",
    { method: "POST", body: JSON.stringify(payload) },
  );
};

// ── /admin/feedback + /admin/error-reports (목록 조회) ──────────────────

export const listFeedback = async (params?: {
  status?: string;
  feedback_type?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: Array<Record<string, unknown>>; total: number }> => {
  // Backend ships ``{feedback: [...], total?: number}`` — wrap into the
  // {items,total} shape every consumer expects.
  const raw = await request<{
    feedback?: Array<Record<string, unknown>>;
    total?: number;
  }>("api/v1/admin/feedback/list", { method: "GET", query: params });
  const items = raw.feedback ?? [];
  return { items, total: raw.total ?? items.length };
};

export const listErrorReports = async (params?: {
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: Array<Record<string, unknown>>; total: number }> => {
  // Backend ships ``{reports: [...]}`` — normalize to {items,total}.
  const raw = await request<{
    reports?: Array<Record<string, unknown>>;
    total?: number;
  }>("api/v1/admin/error-reports", { method: "GET", query: params });
  const items = raw.reports ?? [];
  return { items, total: raw.total ?? items.length };
};

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

export const listDocumentOwners = async (params: {
  kb_id: string;
}): Promise<DocumentOwner[]> => {
  // Backend wraps as ``{owners: [...]}`` and *requires* kb_id (422 otherwise).
  // Caller must pass kb_id — enforce at the type level so we never hit the
  // 422 path silently again.
  const raw = await request<{ owners?: DocumentOwner[] }>(
    "api/v1/admin/ownership/documents",
    { method: "GET", query: params },
  );
  return raw.owners ?? [];
};

export const listStaleOwners = async (params: {
  kb_id: string;
}): Promise<DocumentOwner[]> => {
  // Backend wraps as ``{stale_owners, total, kb_id, days_threshold}``.
  // kb_id is required upstream — see listDocumentOwners.
  const raw = await request<{ stale_owners?: DocumentOwner[] }>(
    "api/v1/admin/ownership/stale",
    { method: "GET", query: params },
  );
  return raw.stale_owners ?? [];
};

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

export const searchOwners = async (params: {
  query: string;
  kb_id?: string;
}): Promise<{ owners: Owner[] }> => {
  // Backend ships ``{experts: [...], total, query}`` — alias as `owners`
  // for the legacy frontend shape.
  const raw = await request<{ experts?: Owner[] }>(
    "api/v1/knowledge/experts/search",
    { method: "GET", query: params },
  );
  return { owners: raw.experts ?? [] };
};

// ── /admin/verification/pending ────────────────────────────────────────

export const listPendingVerifications = async (): Promise<
  Array<Record<string, unknown>>
> => {
  // Backend ships ``{documents, total, page, page_size}``. Consumers want a
  // flat array (composite "대기 작업" view).
  const raw = await request<{
    documents?: Array<Record<string, unknown>>;
  }>("api/v1/admin/verification/pending", { method: "GET" });
  return raw.documents ?? [];
};

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

/** Raw row schema as returned by ``/admin/search/history`` (snake_case). */
type SearchHistoryRawRow = {
  id?: string;
  knowledge_id?: string;
  kb_id?: string | null;
  usage_type?: string | null;
  user_id?: string | null;
  context?: {
    query?: string;
    display_query?: string;
    total_chunks?: number;
    search_time_ms?: number;
  } | null;
  created_at?: string;
};

function adaptSearchHistoryRow(row: SearchHistoryRawRow): SearchHistoryItem {
  const ctx = row.context ?? {};
  // ``kb_id`` is a comma-separated string in the raw row — split it back to
  // an array so the table renders one chip per KB.
  const kbIds =
    typeof row.kb_id === "string" && row.kb_id
      ? row.kb_id.split(",").map((s) => s.trim()).filter(Boolean)
      : [];
  return {
    timestamp: row.created_at ?? "",
    query: ctx.query ?? ctx.display_query ?? row.knowledge_id ?? "",
    result_count: ctx.total_chunks,
    kb_ids: kbIds,
    user_id: row.user_id ?? undefined,
    response_time_ms:
      typeof ctx.search_time_ms === "number"
        ? Math.round(ctx.search_time_ms)
        : undefined,
    source: row.usage_type ?? undefined,
  };
}

export const getSearchHistory = async (params?: {
  page?: number;
  page_size?: number;
}): Promise<{ items: SearchHistoryItem[]; total: number }> => {
  // Backend ships ``{searches: [raw_row...], total, page, page_size}`` where
  // each raw row mixes ``knowledge_id`` (= the query), a comma-separated
  // ``kb_id`` string, and a ``context`` blob. Adapt to the presentational
  // SearchHistoryItem shape so the page can render directly.
  const raw = await request<{
    searches?: SearchHistoryRawRow[];
    total?: number;
  }>("api/v1/admin/search/history", { method: "GET", query: params });
  const items = (raw.searches ?? []).map(adaptSearchHistoryRow);
  return { items, total: raw.total ?? items.length };
};

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
