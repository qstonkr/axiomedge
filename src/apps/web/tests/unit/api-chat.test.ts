import { describe, expect, it, vi, afterEach } from "vitest";

import {
  createConversation,
  deleteConversation,
  listConversations,
  listMessages,
  renameConversation,
  sendMessage,
} from "@/lib/api/chat";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(payload: unknown, status = 200) {
  return vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "content-type": "application/json" },
    }) as unknown as Response,
  );
}

describe("chat api", () => {
  it("createConversation hits POST /chat/conversations", async () => {
    const fetchSpy = mockFetch({ id: "conv-1" }, 201);
    const id = await createConversation({ kb_ids: ["g-espa"] });
    expect(id).toBe("conv-1");
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/proxy\/api\/v1\/chat\/conversations$/),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("listConversations parses response", async () => {
    mockFetch({
      conversations: [{ id: "c1", title: "x", kb_ids: [], updated_at: "now" }],
    });
    const xs = await listConversations();
    expect(xs[0].id).toBe("c1");
  });

  it("renameConversation throws on 404", async () => {
    mockFetch({ detail: "Conversation not found" }, 404);
    await expect(renameConversation("c1", "t")).rejects.toThrow();
  });

  it("deleteConversation succeeds on 200", async () => {
    mockFetch({ status: "ok" });
    await deleteConversation("c1");
  });

  it("listMessages parses chunks/meta", async () => {
    mockFetch({
      messages: [
        {
          id: "m1",
          role: "assistant",
          content: "hi",
          chunks: [],
          meta: { confidence: 0.5 },
          trace_id: null,
          created_at: "now",
        },
      ],
    });
    const xs = await listMessages("c1");
    expect(xs[0].meta.confidence).toBe(0.5);
  });

  it("sendMessage forwards force_mode", async () => {
    const spy = mockFetch({
      id: "m2",
      role: "assistant",
      content: "ok",
      chunks: [],
      meta: {},
      trace_id: null,
      mode_used: "agentic",
    });
    await sendMessage("c1", { content: "Q", force_mode: "deep" });
    const body = JSON.parse(spy.mock.calls[0][1]!.body as string);
    expect(body.force_mode).toBe("deep");
  });
});
