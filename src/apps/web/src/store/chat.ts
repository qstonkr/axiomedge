"use client";

import { create } from "zustand";

type ChatStore = {
  activeConversationId: string | null;
  selectedKbIds: string[];
  resetForConversation: (id: string | null) => void;
  setSelectedKbIds: (ids: string[]) => void;
  toggleKb: (id: string) => void;
};

/**
 * /chat in-memory state. Server is source of truth — chat_messages and
 * chat_conversations persist via /api/v1/chat, and TanStack Query
 * (useMessages / useConversations) handles render state. This store only
 * holds two pieces of UI state that don't belong to the server: which
 * conversation is currently active, and the user's KB chip selection.
 *
 * sessionStorage is removed deliberately to comply with PIPA at-rest
 * encryption — body data never lands in browser storage.
 */
export const useChatStore = create<ChatStore>((set) => ({
  activeConversationId: null,
  selectedKbIds: [],
  resetForConversation: (id) => set({ activeConversationId: id }),
  setSelectedKbIds: (ids) => set({ selectedKbIds: ids }),
  toggleKb: (id) =>
    set((s) => ({
      selectedKbIds: s.selectedKbIds.includes(id)
        ? s.selectedKbIds.filter((x) => x !== id)
        : [...s.selectedKbIds, id],
    })),
}));
