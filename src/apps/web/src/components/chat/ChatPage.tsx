"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { useChatStore } from "@/store/chat";
import {
  useCreateConversation,
  useMessages,
  useSendMessage,
} from "@/store/conversations";

import { ChatInput } from "./ChatInput";
import { ChatMessages } from "./ChatMessages";
import { ConversationSidebar } from "./ConversationSidebar";
import { KbSelector } from "./KbSelector";
import { ModeForceMenu, type ForceMode } from "./ModeForceMenu";
import { RecommendedQueries } from "./RecommendedQueries";
import { SourcePanel, type SourceChunk } from "./SourcePanel";

export function ChatPage({ userEmail }: { userEmail?: string } = {}) {
  const activeId = useChatStore((s) => s.activeConversationId);
  const setActive = useChatStore((s) => s.resetForConversation);
  const selectedKbIds = useChatStore((s) => s.selectedKbIds);

  const create = useCreateConversation();
  const { data: messages = [] } = useMessages(activeId);
  const send = useSendMessage(activeId);
  const [forceMode, setForceMode] = useState<ForceMode>("auto");
  const [highlightMarker, setHighlightMarker] = useState<number | null>(null);
  const [ownerHint, setOwnerHint] = useState(false);

  const params = useSearchParams();
  const showOwnerOnboarding = params?.get("onboarding") === "owner";

  // Show last assistant message's chunks/meta in right panel.
  const lastAssistant = useMemo(
    () => [...messages].reverse().find((m) => m.role === "assistant"),
    [messages],
  );
  const sourceChunks: SourceChunk[] = useMemo(
    () =>
      ((lastAssistant?.chunks ?? []) as unknown as SourceChunk[]).map((c, i) => ({
        ...c,
        marker: c.marker ?? i + 1,
      })),
    [lastAssistant],
  );

  async function ensureConversation(): Promise<string> {
    if (activeId) return activeId;
    const id = await create.mutateAsync({ kb_ids: selectedKbIds });
    setActive(id);
    return id;
  }

  async function handleSubmit(content: string) {
    await ensureConversation();
    // Pass current selectedKbIds so mid-conversation KB toggling actually
    // routes — backend prefers per-message kb_ids over the conversation row.
    await send.mutateAsync({
      content,
      force_mode: forceMode === "auto" ? null : forceMode,
      kb_ids: selectedKbIds,
    });
  }

  // Keyboard: Cmd/Ctrl + N → new chat.
  useEffect(() => {
    const onKey = async (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        const id = await create.mutateAsync({ kb_ids: selectedKbIds });
        setActive(id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [create, setActive, selectedKbIds]);

  return (
    <div className="flex h-full w-full">
      <ConversationSidebar activeId={activeId} onSelect={setActive} userEmail={userEmail} />

      <main className="flex flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border-default px-4 py-2 text-sm">
          <span className="font-medium">
            {messages.length > 0 ? "대화" : "새 대화"}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <KbSelector />
            <ModeForceMenu value={forceMode} onChange={setForceMode} />
          </div>
        </header>

        {showOwnerOnboarding && (
          <div className="border-b border-border-default bg-bg-info px-4 py-2 text-sm">
            💡 오너 검색은 이제 채팅창에서 <code>/owner 이름</code> 으로 가능합니다.
          </div>
        )}

        <section className="flex-1 overflow-y-auto px-6 py-4">
          {messages.length === 0 ? (
            <div className="space-y-4">
              <p className="text-sm text-fg-muted">
                궁금한 것을 물어보세요. <kbd>⌘/Ctrl+N</kbd> 새 대화.
              </p>
              <RecommendedQueries
                onPick={(q) => handleSubmit(q)}
                pending={send.isPending}
              />
            </div>
          ) : (
            <ChatMessages
              messages={messages}
              onMarkerActivate={setHighlightMarker}
              onMarkerDeactivate={() => setHighlightMarker(null)}
              onReportError={() => {/* handled inline by 호버 액션; PR4+ may add modal */}}
              onResubmit={(prior) => prior && handleSubmit(prior)}
              onFindOwner={() => setOwnerHint(true)}
            />
          )}
        </section>

        <footer className="border-t border-border-default px-6 py-3">
          {ownerHint && (
            <p className="mb-1 text-xs text-fg-muted">
              💡 입력창에 <code>/owner 이름</code> 으로 오너를 검색할 수 있습니다.
              <button onClick={() => setOwnerHint(false)} className="ml-2">
                닫기
              </button>
            </p>
          )}
          <ChatInput onSubmit={handleSubmit} pending={send.isPending} />
        </footer>
      </main>

      <SourcePanel
        chunks={sourceChunks}
        meta={(lastAssistant?.meta ?? {}) as Record<string, unknown>}
        highlightedMarker={highlightMarker}
      />
    </div>
  );
}
