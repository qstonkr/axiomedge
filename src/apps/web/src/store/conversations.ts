"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createConversation,
  deleteConversation,
  listConversations,
  listMessages,
  renameConversation,
  sendMessage,
  type ChatMessage,
  type Conversation,
  type SendResult,
} from "@/lib/api/chat";

const KEYS = {
  conversations: ["chat", "conversations"] as const,
  messages: (id: string) => ["chat", "messages", id] as const,
};

export function useConversations() {
  return useQuery<Conversation[]>({
    queryKey: KEYS.conversations,
    queryFn: listConversations,
    staleTime: 30_000,
  });
}

export function useMessages(id: string | null) {
  return useQuery<ChatMessage[]>({
    queryKey: id ? KEYS.messages(id) : ["chat", "messages", "none"],
    queryFn: () => (id ? listMessages(id) : Promise.resolve([])),
    enabled: !!id,
  });
}

export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createConversation,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.conversations }),
  });
}

export function useRenameConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      renameConversation(id, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.conversations }),
  });
}

export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteConversation,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.conversations }),
  });
}

export function useSendMessage(id: string | null) {
  const qc = useQueryClient();
  return useMutation<
    SendResult,
    Error,
    { content: string; force_mode?: "quick" | "deep" | null }
  >({
    mutationFn: (body) => {
      if (!id) throw new Error("conversation id required");
      return sendMessage(id, body);
    },
    onSuccess: () => {
      if (!id) return;
      qc.invalidateQueries({ queryKey: KEYS.messages(id) });
      qc.invalidateQueries({ queryKey: KEYS.conversations });
    },
  });
}

export const conversationsKeys = KEYS;
