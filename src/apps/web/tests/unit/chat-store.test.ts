import { describe, expect, it, beforeEach } from "vitest";
import { useChatStore } from "@/store/chat";

describe("useChatStore (no sessionStorage)", () => {
  beforeEach(() => {
    useChatStore.getState().resetForConversation(null);
    sessionStorage.clear();
  });

  it("starts empty", () => {
    expect(useChatStore.getState().turns).toEqual([]);
  });

  it("resetForConversation sets active id and clears turns", () => {
    useChatStore.getState().appendTurn({
      kind: "user",
      id: "u1",
      query: "hi",
    } as never);
    useChatStore.getState().resetForConversation("conv-1");
    expect(useChatStore.getState().activeConversationId).toBe("conv-1");
    expect(useChatStore.getState().turns).toEqual([]);
  });

  it("does NOT persist to sessionStorage", () => {
    useChatStore.getState().appendTurn({
      kind: "user",
      id: "u1",
      query: "hi",
    } as never);
    const keys = Object.keys(sessionStorage);
    expect(keys.filter((k) => k.startsWith("axiomedge:chat") || k.startsWith("chat-store"))).toEqual([]);
  });
});
