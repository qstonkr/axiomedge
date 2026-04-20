/**
 * Local types for the /chat page. Shape mirrors what the FastAPI
 * /search/hub + /agentic/ask endpoints return so we can render either.
 */

export type ChunkSource = {
  id?: string;
  kb_id?: string;
  document_id?: string;
  document_name?: string;
  title?: string;
  text?: string;
  content?: string;
  tier?: string;
  score?: number;
  rerank_score?: number;
  metadata?: Record<string, unknown>;
  source_uri?: string;
};

export type AssistantTurn = {
  kind: "assistant";
  id: string;
  query: string;
  answer: string;
  chunks: ChunkSource[];
  searched_kbs?: string[];
  failure_reason?: string | null;
  errors?: string[];
  meta?: {
    confidence?: string | number;
    confidence_level?: "HIGH" | "MEDIUM" | "LOW" | "UNCERTAIN" | string;
    crag_action?: string | null;
    query_type?: string;
    search_time_ms?: number;
    iteration_count?: number;
    estimated_cost_usd?: number;
    llm_provider?: string;
    trace_id?: string;
    /** Composite Reranking 분해 — Streamlit `rerank_breakdown` 동치. */
    rerank_breakdown?: {
      dense?: number;
      sparse?: number;
      colbert?: number;
      cross_encoder?: number;
    };
    /** 쿼리 확장된 토큰. */
    expanded_terms?: string[];
    /** 오타 교정 결과. */
    corrected_query?: string;
    original_query?: string;
    /** Working memory probe hit. */
    working_memory_hit?: boolean;
  };
};

export type UserTurn = {
  kind: "user";
  id: string;
  query: string;
};

export type Turn = UserTurn | AssistantTurn;
