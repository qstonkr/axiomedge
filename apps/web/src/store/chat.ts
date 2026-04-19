"use client";

import { create } from "zustand";

export type ChatMode = "agentic" | "fast";

type ChatStore = {
  selectedKbIds: string[];
  mode: ChatMode;
  setSelectedKbIds: (ids: string[]) => void;
  toggleKb: (id: string) => void;
  setMode: (mode: ChatMode) => void;
};

/**
 * Sticky UI state for /chat. Server data (search results, KB catalog) lives
 * in TanStack Query — we only keep selection here so the user's KB scope
 * survives navigating between messages without re-fetching anything.
 */
export const useChatStore = create<ChatStore>((set) => ({
  selectedKbIds: [],
  mode: "agentic",
  setSelectedKbIds: (ids) => set({ selectedKbIds: ids }),
  toggleKb: (id) =>
    set((s) => ({
      selectedKbIds: s.selectedKbIds.includes(id)
        ? s.selectedKbIds.filter((x) => x !== id)
        : [...s.selectedKbIds, id],
    })),
  setMode: (mode) => set({ mode }),
}));
