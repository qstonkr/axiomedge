const BASE = "/api/proxy/api/v1/chat";

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export type Conversation = {
  id: string;
  title: string;
  kb_ids: string[];
  updated_at: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  chunks: Array<Record<string, unknown>>;
  meta: Record<string, unknown>;
  trace_id: string | null;
  created_at: string;
};

export type SendResult = ChatMessage & { mode_used: "search" | "agentic" };

export async function createConversation(
  body: { kb_ids: string[] },
): Promise<string> {
  const r = await jsonFetch<{ id: string }>("/conversations", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return r.id;
}

export async function listConversations(): Promise<Conversation[]> {
  const r = await jsonFetch<{ conversations: Conversation[] }>("/conversations");
  return r.conversations;
}

export async function renameConversation(id: string, title: string): Promise<void> {
  await jsonFetch(`/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export async function deleteConversation(id: string): Promise<void> {
  await jsonFetch(`/conversations/${id}`, { method: "DELETE" });
}

export async function listMessages(id: string): Promise<ChatMessage[]> {
  const r = await jsonFetch<{ messages: ChatMessage[] }>(
    `/conversations/${id}/messages`,
  );
  return r.messages;
}

export async function sendMessage(
  id: string,
  body: { content: string; force_mode?: "quick" | "deep" | null },
): Promise<SendResult> {
  return jsonFetch<SendResult>(`/conversations/${id}/messages`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
