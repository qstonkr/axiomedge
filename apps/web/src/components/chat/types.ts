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
  meta?: {
    confidence?: string | number;
    crag_action?: string | null;
    query_type?: string;
    search_time_ms?: number;
    iteration_count?: number;
    estimated_cost_usd?: number;
    llm_provider?: string;
    trace_id?: string;
  };
};

export type UserTurn = {
  kind: "user";
  id: string;
  query: string;
};

export type Turn = UserTurn | AssistantTurn;
