import { describe, it, expect, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useConversations, useSendMessage } from "@/store/conversations";

vi.mock("@/lib/api/chat", () => ({
  listConversations: vi.fn().mockResolvedValue([
    { id: "c1", title: "x", kb_ids: [], updated_at: "now" },
  ]),
  sendMessage: vi.fn().mockResolvedValue({
    id: "m1",
    role: "assistant",
    content: "hi",
    chunks: [],
    meta: {},
    trace_id: null,
    mode_used: "search",
  }),
  createConversation: vi.fn(),
  renameConversation: vi.fn(),
  deleteConversation: vi.fn(),
  listMessages: vi.fn().mockResolvedValue([]),
}));

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("useConversations", () => {
  it("fetches list", async () => {
    const { result } = renderHook(() => useConversations(), { wrapper: wrap() });
    await waitFor(() => expect(result.current.data?.length).toBe(1));
    expect(result.current.data![0].id).toBe("c1");
  });
});

describe("useSendMessage", () => {
  it("invalidates conversations on success", async () => {
    const { result } = renderHook(() => useSendMessage("c1"), { wrapper: wrap() });
    const sent = await result.current.mutateAsync({ content: "Q" });
    expect(sent.id).toBe("m1");
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});
