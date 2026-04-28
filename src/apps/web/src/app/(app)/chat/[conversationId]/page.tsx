"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";

import { ChatPage } from "@/components/chat/ChatPage";
import { useChatStore } from "@/store/chat";

export default function ChatConversationPage() {
  const params = useParams<{ conversationId: string }>();
  const conversationId = params?.conversationId ?? null;
  const setActive = useChatStore((s) => s.resetForConversation);

  useEffect(() => {
    if (conversationId) setActive(conversationId);
  }, [conversationId, setActive]);

  return <ChatPage />;
}
