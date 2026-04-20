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

/**
 * 한 personal KB 안의 문서 목록 (owner 만 호출 가능). Backend 가
 * Qdrant payload 를 scroll 하므로 정확한 row 모양은 파일별로 다양 —
 * 자주 쓰는 키 (`document_id`, `document_name`, `created_at`, `chunk_count`)
 * 만 typed 하고 나머지는 unknown 으로 둠.
 */
export type KbDocument = {
  document_id?: string;
  document_name?: string;
  doc_id?: string;
  source?: string;
  source_type?: string;
  created_at?: string;
  chunk_count?: number;
  [k: string]: unknown;
};

export const listKbDocuments = (
  kbId: string,
  params?: { page?: number; page_size?: number },
) =>
  request<{
    documents: KbDocument[];
    total: number;
    page: number;
    page_size: number;
    kb_id: string;
  }>(`api/v1/kb/${encodeURIComponent(kbId)}/documents`, {
    method: "GET",
    query: params,
  });

// ── /search-groups ──────────────────────────────────────────────────────

export type SearchGroup = {
  id: string;
  name: string;
  kb_ids: string[];
  description?: string;
  is_default?: boolean;
  created_by?: string;
  created_at?: string;
};

export const listSearchGroups = async (): Promise<{ groups: SearchGroup[] }> => {
  // backend 는 ``/distill/search-groups`` 가 정답 — ``/search-groups`` 는 404
  const raw = await request<{ groups?: SearchGroup[] }>(
    "api/v1/distill/search-groups",
    { method: "GET" },
  );
  return { groups: raw.groups ?? [] };
};

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

/**
 * Backend `feedback` row 의 정규화된 모양. backend 는 일부 필드가 빠진
 * row 도 보낼 수 있으므로 모든 필드 optional. 표시 단(my-feedback) 에서
 * 빠진 필드는 default 라벨/공란으로 표시.
 */
export type FeedbackItem = {
  id?: string;
  feedback_type?: string;
  status?: string;
  content?: string;
  document_id?: string | null;
  created_at?: string;
  user_id?: string;
};

/** Backend `error_report` row 정규화 — 동일하게 모든 필드 optional. */
export type ErrorReportItem = {
  id?: string;
  error_type?: string;
  priority?: string;
  title?: string;
  description?: string;
  status?: string;
  document_id?: string | null;
  created_at?: string;
  user_id?: string;
};

/**
 * Admin-scope feedback list — `feedback:review` 권한 (ADMIN/OWNER) 필요.
 * 운영자 화면 (`/admin/errors` 등) 에서 호출.
 */
export const listFeedback = async (params?: {
  status?: string;
  feedback_type?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: FeedbackItem[]; total: number }> => {
  const raw = await request<{
    feedback?: FeedbackItem[];
    total?: number;
  }>("api/v1/admin/feedback/list", { method: "GET", query: params });
  const items = raw.feedback ?? [];
  return { items, total: raw.total ?? items.length };
};

export const listErrorReports = async (params?: {
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: ErrorReportItem[]; total: number }> => {
  const raw = await request<{
    reports?: ErrorReportItem[];
    total?: number;
  }>("api/v1/admin/error-reports", { method: "GET", query: params });
  const items = raw.reports ?? [];
  return { items, total: raw.total ?? items.length };
};

/**
 * User-scope ("내 것만") feedback list — `feedback:submit` 권한이면 OK
 * (MEMBER/VIEWER 도 호출 가능). `/my-feedback`, `/my-documents` 의 대기
 * 작업 탭이 호출. backend 에서 `user_id == caller.sub` 필터링.
 */
export const listMyFeedback = async (params?: {
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: FeedbackItem[]; total: number }> => {
  const raw = await request<{
    feedback?: FeedbackItem[];
    total?: number;
  }>("api/v1/knowledge/feedback/my", { method: "GET", query: params });
  const items = raw.feedback ?? [];
  return { items, total: raw.total ?? items.length };
};

/** User-scope error-report list — listMyFeedback 와 같은 사유. */
export const listMyErrorReports = async (params?: {
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: ErrorReportItem[]; total: number }> => {
  const raw = await request<{
    reports?: ErrorReportItem[];
    total?: number;
  }>("api/v1/knowledge/error-reports/my", { method: "GET", query: params });
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
}): Promise<{ owners: Owner[]; partial_errors?: string[] }> => {
  // Backend ships ``{experts: [...], total, query, partial_errors?}`` —
  // alias `experts` as `owners` for the legacy frontend shape, and
  // surface partial_errors so the UI can warn about degraded results
  // (e.g. graph search succeeded but document_owner search failed).
  const raw = await request<{
    experts?: Owner[];
    partial_errors?: string[];
  }>("api/v1/knowledge/experts/search", { method: "GET", query: params });
  const out: { owners: Owner[]; partial_errors?: string[] } = {
    owners: raw.experts ?? [],
  };
  if (raw.partial_errors && raw.partial_errors.length > 0) {
    out.partial_errors = raw.partial_errors;
  }
  return out;
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

// ── /admin/data-sources (B-2 콘텐츠 관리) ───────────────────────────────

export type DataSource = {
  id: string;
  name: string;
  source_type: string;
  kb_id?: string | null;
  schedule?: string | null;
  status?: string | null;
  created_at?: string;
  updated_at?: string;
  last_sync_at?: string | null;
  last_sync_result?: Record<string, unknown> | null;
  error_message?: string | null;
  metadata?: Record<string, unknown> | null;
  crawl_config?: Record<string, unknown> | null;
  pipeline_config?: Record<string, unknown> | null;
};

export const listDataSources = async (): Promise<DataSource[]> => {
  // Backend ships ``{sources: [...]}``.
  const raw = await request<{ sources?: DataSource[] }>(
    "api/v1/admin/data-sources",
    { method: "GET" },
  );
  return raw.sources ?? [];
};

export const triggerDataSourceSync = (sourceId: string) =>
  request<{ success: boolean; job_id?: string; message?: string }>(
    `api/v1/admin/data-sources/${encodeURIComponent(sourceId)}/trigger`,
    { method: "POST" },
  );

export type DataSourceUpsertBody = {
  name: string;
  source_type: string;
  kb_id?: string | null;
  schedule?: string | null;
  crawl_config?: Record<string, unknown> | null;
  pipeline_config?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
};

export const createDataSource = (body: DataSourceUpsertBody) =>
  request<DataSource>("api/v1/admin/data-sources", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateDataSource = (
  sourceId: string,
  body: Partial<DataSourceUpsertBody>,
) =>
  request<DataSource>(
    `api/v1/admin/data-sources/${encodeURIComponent(sourceId)}`,
    { method: "PUT", body: JSON.stringify(body) },
  );

export const deleteDataSource = (sourceId: string) =>
  request<{ success: boolean }>(
    `api/v1/admin/data-sources/${encodeURIComponent(sourceId)}`,
    { method: "DELETE" },
  );

export const getDataSourceStatus = (sourceId: string) =>
  request<Record<string, unknown>>(
    `api/v1/admin/data-sources/${encodeURIComponent(sourceId)}/status`,
    { method: "GET" },
  );

// ── /admin/pipeline/status (B-2 ingest) ─────────────────────────────────

export type PipelineRun = {
  id?: string;
  run_id?: string;
  kb_id?: string;
  source_type?: string;
  source_name?: string;
  status?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  document_count?: number | null;
  error_message?: string | null;
};

export type PipelineStatus = {
  status: string;
  active_runs: number;
  queued: number;
  last_run?: PipelineRun | null;
  recent_runs?: PipelineRun[];
};

export const getPipelineStatus = () =>
  request<PipelineStatus>("api/v1/admin/pipeline/status", { method: "GET" });

export const getPipelineMetrics = () =>
  request<Record<string, unknown>>("api/v1/admin/pipeline/metrics", {
    method: "GET",
  });

// ── /admin/glossary (B-2 용어집) ────────────────────────────────────────

export type GlossaryTerm = {
  id: string;
  term_id?: string;
  kb_id: string;
  term: string;
  term_ko?: string | null;
  definition?: string | null;
  synonyms?: string[];
  domain?: string | null;
  source?: string | null;
  status?: string | null;
  created_at?: string;
  updated_at?: string;
};

export const listGlossaryTerms = async (params?: {
  kb_id?: string;
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: GlossaryTerm[]; total: number }> => {
  const raw = await request<{ terms?: GlossaryTerm[]; total?: number }>(
    "api/v1/admin/glossary",
    { method: "GET", query: params },
  );
  const items = raw.terms ?? [];
  return { items, total: raw.total ?? items.length };
};

export type GlossaryUpsertBody = {
  kb_id: string;
  term: string;
  term_ko?: string | null;
  definition?: string | null;
  synonyms?: string[];
  domain?: string | null;
};

export const createGlossaryTerm = (body: GlossaryUpsertBody) =>
  request<GlossaryTerm>("api/v1/admin/glossary", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateGlossaryTerm = (
  termId: string,
  body: Partial<GlossaryUpsertBody>,
) =>
  request<GlossaryTerm>(`api/v1/admin/glossary/${encodeURIComponent(termId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteGlossaryTerm = (termId: string) =>
  request<{ success: boolean }>(
    `api/v1/admin/glossary/${encodeURIComponent(termId)}`,
    { method: "DELETE" },
  );

export const approveGlossaryTerm = (termId: string) =>
  request<{ success: boolean }>(
    `api/v1/admin/glossary/${encodeURIComponent(termId)}/approve`,
    { method: "POST" },
  );

export const rejectGlossaryTerm = (termId: string, reason?: string) =>
  request<{ success: boolean }>(
    `api/v1/admin/glossary/${encodeURIComponent(termId)}/reject`,
    {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? "" }),
    },
  );

// ── /admin/dedup/conflicts (B-2 중복/모순) ──────────────────────────────

export type DedupConflict = {
  id?: string;
  kb_id?: string;
  doc_a?: string;
  doc_b?: string;
  similarity?: number;
  conflict_type?: string;
  status?: string;
  resolved_at?: string | null;
  detected_at?: string | null;
};

export const listDedupConflicts = async (params?: {
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: DedupConflict[]; total: number }> => {
  const raw = await request<{
    conflicts?: DedupConflict[];
    total?: number;
  }>("api/v1/admin/dedup/conflicts", { method: "GET", query: params });
  const items = raw.conflicts ?? [];
  return { items, total: raw.total ?? items.length };
};

export const resolveDedupConflict = (body: {
  conflict_id: string;
  resolution: "keep_a" | "keep_b" | "merge" | "ignore";
  note?: string;
}) =>
  request<{ success: boolean }>("api/v1/admin/dedup/resolve", {
    method: "POST",
    body: JSON.stringify(body),
  });

// ── /admin/dedup/stats + /eval (B-2 RAG 품질) ───────────────────────────

export type DedupStats = {
  total_duplicates_found: number;
  total_resolved: number;
  pending: number;
  stages?: Record<string, { checked: number; flagged: number }>;
  pipeline_metrics?: Record<string, unknown>;
};

export const getDedupStats = () =>
  request<DedupStats>("api/v1/admin/dedup/stats", { method: "GET" });

export type EvalStatus = {
  status: string;
  current_eval_id?: string | null;
  progress?: number;
  message?: string | null;
};

export const getEvalStatus = () =>
  request<EvalStatus>("api/v1/admin/eval/status", { method: "GET" });

export type EvalRun = {
  id?: string;
  eval_id?: string;
  kb_id?: string | null;
  status?: string;
  started_at?: string | null;
  completed_at?: string | null;
  metrics?: Record<string, number> | null;
};

export const listEvalHistory = async (params?: {
  page?: number;
  page_size?: number;
}): Promise<{ items: EvalRun[]; total: number }> => {
  const raw = await request<{ evaluations?: EvalRun[]; total?: number }>(
    "api/v1/admin/eval/history",
    { method: "GET", query: params },
  );
  const items = raw.evaluations ?? [];
  return { items, total: raw.total ?? items.length };
};

export const triggerEval = (body: { kb_id?: string | null }) =>
  request<{ success: boolean; eval_id?: string }>("api/v1/admin/eval/trigger", {
    method: "POST",
    body: JSON.stringify(body),
  });

// ── /admin/golden-set (B-2 Golden Q&A) ──────────────────────────────────

export type GoldenItem = {
  id: string;
  kb_id?: string;
  question: string;
  answer?: string;
  contexts?: string[];
  tags?: string[];
  difficulty?: string | null;
  status?: "approved" | "pending" | "rejected" | string;
  created_at?: string;
};

export const listGoldenSet = async (params?: {
  kb_id?: string;
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: GoldenItem[]; total: number }> => {
  const raw = await request<{ items?: GoldenItem[]; total?: number }>(
    "api/v1/admin/golden-set",
    { method: "GET", query: params },
  );
  const items = raw.items ?? [];
  return { items, total: raw.total ?? items.length };
};

export const deleteGoldenItem = (itemId: string) =>
  request<{ success: boolean }>(
    `api/v1/admin/golden-set/${encodeURIComponent(itemId)}`,
    { method: "DELETE" },
  );

/**
 * Golden set 항목의 status 변경 등 부분 업데이트 (PATCH).
 * Streamlit 의 "승인 / 거부" 버튼이 호출하던 endpoint.
 */
export const updateGoldenItem = (
  itemId: string,
  body: Partial<{
    status: "approved" | "pending" | "rejected";
    question: string;
    answer: string;
  }>,
) =>
  request<{ success: boolean; item?: GoldenItem }>(
    `api/v1/admin/golden-set/${encodeURIComponent(itemId)}`,
    { method: "PATCH", body: JSON.stringify(body) },
  );

// ── /agentic/traces (B-2 Agent Trace viewer) ────────────────────────────

export type AgentTraceListItem = {
  trace_id: string;
  query: string;
  answer_preview?: string;
  llm_provider?: string;
  iteration_count?: number;
  total_duration_ms?: number;
};

export const listAgentTraces = async (limit = 50): Promise<{
  count: number;
  traces: AgentTraceListItem[];
}> => {
  return request<{ count: number; traces: AgentTraceListItem[] }>(
    "api/v1/agentic/traces",
    { method: "GET", query: { limit } },
  );
};

export const getAgentTrace = (traceId: string) =>
  request<Record<string, unknown>>(
    `api/v1/agentic/traces/${encodeURIComponent(traceId)}`,
    { method: "GET" },
  );

// ── /auth/users (B-2 사용자 관리) ───────────────────────────────────────

export type AuthUser = {
  id: string;
  email: string;
  display_name?: string | null;
  provider?: string;
  department?: string | null;
  is_active?: boolean;
  created_at?: string;
};

export const listAuthUsers = async (params?: {
  limit?: number;
  offset?: number;
}): Promise<{ users: AuthUser[]; total?: number }> => {
  const raw = await request<{ users?: AuthUser[]; total?: number }>(
    "api/v1/auth/users",
    { method: "GET", query: params },
  );
  return { users: raw.users ?? [], total: raw.total };
};

export type AuthRole = {
  id?: string;
  name: string;
  display_name?: string | null;
  description?: string | null;
};

export const listAuthRoles = async (): Promise<AuthRole[]> => {
  const raw = await request<{ roles?: AuthRole[] }>("api/v1/auth/roles", {
    method: "GET",
  });
  return raw.roles ?? [];
};

export type AuthUserUpsertBody = {
  email: string;
  display_name?: string | null;
  department?: string | null;
  password?: string;
  is_active?: boolean;
};

export const createAuthUser = (body: AuthUserUpsertBody) =>
  request<AuthUser>("api/v1/auth/users", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateAuthUser = (
  userId: string,
  body: Partial<AuthUserUpsertBody>,
) =>
  request<AuthUser>(`api/v1/auth/users/${encodeURIComponent(userId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const deleteAuthUser = (userId: string) =>
  request<{ success: boolean }>(
    `api/v1/auth/users/${encodeURIComponent(userId)}`,
    { method: "DELETE" },
  );

export const assignAuthRole = (userId: string, role: string) =>
  request<{ success: boolean }>(
    `api/v1/auth/users/${encodeURIComponent(userId)}/roles`,
    { method: "POST", body: JSON.stringify({ role }) },
  );

export const revokeAuthRole = (userId: string, role: string) =>
  request<{ success: boolean }>(
    `api/v1/auth/users/${encodeURIComponent(userId)}/roles/${encodeURIComponent(role)}`,
    { method: "DELETE" },
  );

// ── search-groups CRUD ──────────────────────────────────────────────────

export type SearchGroupUpsertBody = {
  name: string;
  kb_ids: string[];
  description?: string;
  is_default?: boolean;
};

export const createSearchGroup = (body: SearchGroupUpsertBody) =>
  request<{ success: boolean; group: SearchGroup }>(
    "api/v1/distill/search-groups",
    { method: "POST", body: JSON.stringify(body) },
  );

export const updateSearchGroup = (
  groupId: string,
  body: SearchGroupUpsertBody,
) =>
  request<{ success: boolean; group: SearchGroup }>(
    `api/v1/distill/search-groups/${encodeURIComponent(groupId)}`,
    { method: "PUT", body: JSON.stringify(body) },
  );

export const deleteSearchGroup = (groupId: string) =>
  request<{ success: boolean }>(
    `api/v1/distill/search-groups/${encodeURIComponent(groupId)}`,
    { method: "DELETE" },
  );

// ── /distill/edge-servers (B-2 Edge fleet) ──────────────────────────────

export type EdgeServer = {
  id: string;
  store_id: string;
  profile_name?: string;
  display_name?: string | null;
  status?: string;
  last_heartbeat?: string | null;
  server_ip?: string | null;
  os_type?: string | null;
  app_version?: string | null;
  model_version?: string | null;
  ram_total_mb?: number | null;
  ram_used_mb?: number | null;
  disk_free_mb?: number | null;
  avg_latency_ms?: number | null;
  total_queries?: number;
  success_count?: number;
};

export const listEdgeServers = async (): Promise<EdgeServer[]> => {
  const raw = await request<{ items?: EdgeServer[] }>(
    "api/v1/distill/edge-servers",
    { method: "GET" },
  );
  return raw.items ?? [];
};

// ── /admin/knowledge/ingest/jobs (B-2 작업 모니터) ──────────────────────

export type IngestRun = {
  id: string;
  run_id?: string;
  kb_id?: string;
  source_type?: string;
  source_name?: string;
  status?: string;
  documents_fetched?: number;
  documents_ingested?: number;
  documents_held?: number;
  documents_rejected?: number;
  chunks_stored?: number;
  chunks_deduped?: number;
  started_at?: string | null;
  completed_at?: string | null;
  errors?: string[];
};

export const listIngestRuns = async (): Promise<IngestRun[]> => {
  const raw = await request<{ runs?: IngestRun[] }>(
    "api/v1/admin/knowledge/ingest/jobs",
    { method: "GET" },
  );
  return raw.runs ?? [];
};

export const cancelIngestRun = (runId: string) =>
  request<{ success: boolean }>(
    `api/v1/admin/knowledge/ingest/jobs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );

/**
 * 수동 인제스천 트리거 — Streamlit ingestion_jobs.py 의 trigger_ingestion_form
 * 패턴 이식. KB + source_type 필수, description 선택. 성공 시 ``run_id`` 반환.
 */
export type TriggerIngestionBody = {
  kb_id: string;
  source_type:
    | "CONFLUENCE"
    | "JIRA"
    | "GIT"
    | "TEAMS"
    | "GWIKI"
    | "SHAREPOINT"
    | "MANUAL";
  description?: string;
};

export const triggerIngestion = (body: TriggerIngestionBody) =>
  request<{ run_id?: string; id?: string; success?: boolean }>(
    "api/v1/admin/knowledge/ingest",
    { method: "POST", body: JSON.stringify(body) },
  );

// ── /admin/config/weights (B-2 가중치 설정) ─────────────────────────────

export const getConfigWeights = () =>
  request<Record<string, unknown>>("api/v1/admin/config/weights", {
    method: "POST", // backend 는 POST = 조회 (legacy quirk)
    body: JSON.stringify({}),
  });

// ── /admin/graph/stats (B-2 그래프 탐색) ───────────────────────────────

export type GraphStats = {
  node_types?: Record<string, number>;
  edge_types?: Record<string, number>;
  total_nodes?: number;
  total_edges?: number;
};

export const getGraphStats = () =>
  request<GraphStats>("api/v1/admin/graph/stats", { method: "GET" });

export type GraphSearchHit = {
  entity_id?: string;
  entity_name: string;
  entity_type?: string;
  related_count?: number;
  kb_id?: string;
};

export const searchGraphEntities = (body: {
  query: string;
  entity_types?: string[];
  limit?: number;
}) =>
  request<{ hits?: GraphSearchHit[]; results?: GraphSearchHit[] }>(
    "api/v1/admin/graph/search",
    { method: "POST", body: JSON.stringify(body) },
  );

// ── /admin/pipeline/gates (B-2 ingest 게이트) ───────────────────────────

export type PipelineGateStat = {
  gate?: string;
  gate_id?: string;
  total_checks?: number;
  blocked?: number;
  passed?: number;
  block_rate?: number;
};

export type PipelineGatesStats = {
  gates: PipelineGateStat[];
  total_blocked: number;
  total_passed: number;
};

export const getPipelineGatesStats = () =>
  request<PipelineGatesStats>("api/v1/admin/pipeline/gates/stats", {
    method: "GET",
  });

export type BlockedDocument = {
  document_id?: string;
  kb_id?: string;
  gate?: string;
  reason?: string;
  blocked_at?: string;
  status?: string;
};

export const getPipelineGatesBlocked = async (): Promise<BlockedDocument[]> => {
  const raw = await request<{ blocked_documents?: BlockedDocument[] }>(
    "api/v1/admin/pipeline/gates/blocked",
    { method: "GET" },
  );
  return raw.blocked_documents ?? [];
};

// ── /admin/kb/{kb_id}/lifecycle (B-2 doc lifecycle) ─────────────────────

export type KbLifecycleEvent = {
  ts?: string;
  event?: string;
  actor?: string;
  detail?: string;
};

export type KbLifecycle = {
  kb_id: string;
  stage: string;
  created_at?: string | null;
  last_updated?: string | null;
  events?: KbLifecycleEvent[];
  draft_count?: number;
  published_count?: number;
  archived_count?: number;
  scheduled_archive?: Array<{
    document_id: string;
    archive_at: string;
    reason?: string;
  }>;
};

export const getKbLifecycle = (kbId: string) =>
  request<KbLifecycle>(
    `api/v1/admin/kb/${encodeURIComponent(kbId)}/lifecycle`,
    { method: "GET" },
  );

// ── /admin/transparency/stats (B-2 quality 보강) ────────────────────────

export type TransparencyStats = {
  total_documents: number;
  total_citations: number;
  with_provenance: number;
  with_owner: number;
  verified: number;
  transparency_score: number;
  source_coverage_rate: number;
  avg_sources_per_response: number;
};

export const getTransparencyStats = () =>
  request<TransparencyStats>("api/v1/admin/transparency/stats", {
    method: "GET",
  });

// ── /admin/verification/{doc_id}/vote (B-2 verification 투표) ──────────

export const submitVerificationVote = (
  docId: string,
  body: { vote_type: "upvote" | "downvote"; kb_id?: string; user_id?: string },
) =>
  request<{
    success: boolean;
    doc_id: string;
    vote_type: string;
    new_kts_score?: number | null;
    confidence_tier?: string | null;
  }>(`api/v1/admin/verification/${encodeURIComponent(docId)}/vote`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// ── /distill/profiles + base-models + builds + training-data + edge ────
// (B-2 Phase 8 — /admin/edge 6 탭)

export type DistillProfile = {
  name: string;
  enabled: boolean;
  description?: string;
  search_group?: string;
  base_model?: string;
  lora?: { r?: number; alpha?: number; dropout?: number };
  deploy?: {
    quantize?: string;
    s3_bucket?: string;
    s3_prefix?: string;
    auto_update_cron?: string;
  };
  qa_style?: { mode?: string; max_answer_tokens?: number };
  training?: {
    epochs?: number;
    batch_size?: number;
    learning_rate?: number;
    gradient_accumulation?: number;
    max_seq_length?: number;
  };
  data_quality?: Record<string, unknown>;
};

export const listDistillProfiles = async (): Promise<DistillProfile[]> => {
  // backend ships ``{profiles: {name: profile}}`` (dict)
  const raw = await request<{ profiles?: Record<string, DistillProfile> }>(
    "api/v1/distill/profiles",
    { method: "GET" },
  );
  return Object.values(raw.profiles ?? {});
};

export const getDistillProfile = (name: string) =>
  request<DistillProfile>(
    `api/v1/distill/profiles/${encodeURIComponent(name)}`,
    { method: "GET" },
  );

export const deleteDistillProfile = (name: string) =>
  request<{ success: boolean }>(
    `api/v1/distill/profiles/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );

export type DistillProfileCreateBody = {
  name: string;
  search_group: string;
  base_model: string;
  description?: string;
  enabled?: boolean;
  lora?: { r?: number; alpha?: number; dropout?: number };
  training?: {
    epochs?: number;
    batch_size?: number;
    learning_rate?: number;
    gradient_accumulation?: number;
    max_seq_length?: number;
  };
  qa_style?: { mode?: string; max_answer_tokens?: number };
  data_quality?: Record<string, unknown>;
  deploy?: {
    quantize?: string;
    s3_bucket?: string;
    s3_prefix?: string;
    auto_update_cron?: string;
  };
};

export type DistillProfileUpdateBody = Partial<
  Omit<DistillProfileCreateBody, "name">
>;

export const createDistillProfile = (body: DistillProfileCreateBody) =>
  request<DistillProfile>("api/v1/distill/profiles", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateDistillProfile = (
  name: string,
  body: DistillProfileUpdateBody,
) =>
  request<DistillProfile>(
    `api/v1/distill/profiles/${encodeURIComponent(name)}`,
    { method: "PUT", body: JSON.stringify(body) },
  );

// ── base-models ──

export type BaseModel = {
  hf_id: string;
  display_name: string;
  params?: string;
  license?: string;
  commercial_use?: boolean;
  verified?: boolean;
  notes?: string;
  enabled?: boolean;
  sort_order?: number;
};

export const listBaseModels = async (
  enabledOnly = false,
): Promise<BaseModel[]> => {
  const raw = await request<{ models?: BaseModel[] }>(
    "api/v1/distill/base-models",
    { method: "GET", query: { enabled_only: enabledOnly ? 1 : 0 } },
  );
  return raw.models ?? [];
};

export const upsertBaseModel = (body: BaseModel) =>
  request<BaseModel>("api/v1/distill/base-models", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const deleteBaseModel = (hfId: string) =>
  request<{ success: boolean }>(
    `api/v1/distill/base-models/${encodeURIComponent(hfId)}`,
    { method: "DELETE" },
  );

// ── builds (학습/배포) ──

export type DistillBuild = {
  id: string;
  profile_name?: string;
  status?: string;
  version?: string;
  search_group?: string;
  base_model?: string;
  training_samples?: number;
  data_sources?: string;
  train_loss?: number | null;
  eval_loss?: number | null;
  training_duration_sec?: number | null;
  created_at?: string;
};

export const listDistillBuilds = async (): Promise<DistillBuild[]> => {
  const raw = await request<{ items?: DistillBuild[] }>("api/v1/distill/builds", {
    method: "GET",
  });
  return raw.items ?? [];
};

export const triggerRetrain = (body: { profile_name: string }) =>
  request<{ success: boolean; build_id?: string }>("api/v1/distill/retrain", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const deployBuild = (buildId: string) =>
  request<{ success: boolean }>(
    `api/v1/distill/builds/${encodeURIComponent(buildId)}/deploy`,
    { method: "POST" },
  );

export const rollbackBuild = (buildId: string) =>
  request<{ success: boolean }>(
    `api/v1/distill/builds/${encodeURIComponent(buildId)}/rollback`,
    { method: "POST" },
  );

export const deleteBuild = (buildId: string) =>
  request<{ success: boolean }>(
    `api/v1/distill/builds/${encodeURIComponent(buildId)}`,
    { method: "DELETE" },
  );

// ── training-data (데이터 큐레이션) ──

export type TrainingDataStats = {
  profile_name: string;
  total: number;
  approved: number;
  pending: number;
  rejected: number;
  by_source?: Record<string, number>;
  by_batch?: Record<string, number>;
};

export const getTrainingDataStats = (profileName: string) =>
  request<TrainingDataStats>("api/v1/distill/training-data/stats", {
    method: "GET",
    query: { profile_name: profileName },
  });

export const triggerGenerateTrainingData = (body: {
  profile_name: string;
  num_samples?: number;
}) =>
  request<{ success: boolean; batch_id?: string }>(
    "api/v1/distill/training-data/generate",
    { method: "POST", body: JSON.stringify(body) },
  );

// ── edge-servers (서버 운영 — list 는 useOps 의 useEdgeServers) ──

export const deleteEdgeServer = (storeId: string) =>
  request<{ success: boolean }>(
    `api/v1/distill/edge-servers/${encodeURIComponent(storeId)}`,
    { method: "DELETE" },
  );

export const requestEdgeUpdate = (storeId: string) =>
  request<{ success: boolean }>(
    `api/v1/distill/edge-servers/${encodeURIComponent(storeId)}/request-update`,
    { method: "POST" },
  );

// ── manifest (운영/배포) ──

export type EdgeManifest = {
  profile_name: string;
  version?: string;
  model_url?: string;
  model_sha256?: string;
  app_version?: string;
  base_model?: string;
};

export const getEdgeManifest = (profileName: string) =>
  request<EdgeManifest>(
    `api/v1/distill/manifest/${encodeURIComponent(profileName)}`,
    { method: "GET" },
  );

// ── /admin/dashboard/summary (B-2 운영 대시보드) ─────────────────────────

export type AdminDashboardSummary = {
  active_kbs: number | null;
  total_documents: number | null;
  total_chunks: number | null;
  feedback_pending: number | null;
  error_reports_pending: number | null;
  search_history_24h: number | null;
  errors: string[];
};

export const getAdminDashboardSummary = () =>
  request<AdminDashboardSummary>("api/v1/admin/dashboard/summary", {
    method: "GET",
  });

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
