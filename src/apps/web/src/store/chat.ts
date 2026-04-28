"use client";

import { create } from "zustand";

import type { AssistantTurn, UserTurn } from "@/components/chat/types";

type Turn = UserTurn | AssistantTurn;

/** @deprecated removed in PR3 alongside ModeToggle — server-side route_query replaces it */
export type ChatMode = "agentic" | "fast";

type ChatStore = {
  activeConversationId: string | null;
  turns: Turn[];
  selectedKbIds: string[];
  /** @deprecated removed in PR3 — server routes via route_query */
  mode: ChatMode;
  resetForConversation: (id: string | null) => void;
  appendTurn: (turn: Turn) => void;
  hydrateTurns: (turns: Turn[]) => void;
  setSelectedKbIds: (ids: string[]) => void;
  toggleKb: (id: string) => void;
  /** @deprecated removed in PR3 — equivalent to resetForConversation(null) */
  clearTurns: () => void;
  /** @deprecated removed in PR3 — server route_query replaces user-facing toggle */
  setMode: (mode: ChatMode) => void;
};

/**
 * /chat in-memory state. Server is source of truth — chat_messages and
 * chat_conversations persist via /api/v1/chat. sessionStorage is removed
 * deliberately to comply with PIPA at-rest encryption (PR1+) — body data
 * never lands in browser storage.
 */
export const useChatStore = create<ChatStore>((set) => ({
  activeConversationId: null,
  turns: [],
  selectedKbIds: [],
  mode: "agentic",
  resetForConversation: (id) => set({ activeConversationId: id, turns: [] }),
  appendTurn: (turn) => set((s) => ({ turns: [...s.turns, turn] })),
  hydrateTurns: (turns) => set({ turns }),
  setSelectedKbIds: (ids) => set({ selectedKbIds: ids }),
  toggleKb: (id) =>
    set((s) => ({
      selectedKbIds: s.selectedKbIds.includes(id)
        ? s.selectedKbIds.filter((x) => x !== id)
        : [...s.selectedKbIds, id],
    })),
  clearTurns: () => set({ turns: [] }),
  setMode: (mode) => set({ mode }),
}));
