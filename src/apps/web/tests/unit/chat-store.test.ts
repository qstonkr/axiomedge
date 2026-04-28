import { describe, expect, it, beforeEach } from "vitest";
import { useChatStore } from "@/store/chat";

describe("useChatStore (server-SoT, no sessionStorage)", () => {
  beforeEach(() => {
    useChatStore.getState().resetForConversation(null);
    useChatStore.getState().setSelectedKbIds([]);
    sessionStorage.clear();
  });

  it("starts with no active conversation and empty KB selection", () => {
    const s = useChatStore.getState();
    expect(s.activeConversationId).toBeNull();
    expect(s.selectedKbIds).toEqual([]);
  });

  it("resetForConversation sets active id", () => {
    useChatStore.getState().resetForConversation("conv-1");
    expect(useChatStore.getState().activeConversationId).toBe("conv-1");
  });

  it("toggleKb adds and removes a KB id", () => {
    useChatStore.getState().toggleKb("kb-a");
    expect(useChatStore.getState().selectedKbIds).toEqual(["kb-a"]);
    useChatStore.getState().toggleKb("kb-a");
    expect(useChatStore.getState().selectedKbIds).toEqual([]);
  });

  it("does NOT persist to sessionStorage", () => {
    useChatStore.getState().resetForConversation("conv-1");
    useChatStore.getState().toggleKb("kb-a");
    const keys = Object.keys(sessionStorage);
    expect(
      keys.filter(
        (k) => k.startsWith("axiomedge:chat") || k.startsWith("chat-store"),
      ),
    ).toEqual([]);
  });
});
