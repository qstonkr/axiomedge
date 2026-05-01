"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Lightbulb, MessagesSquare, Paperclip } from "lucide-react";

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
  // mobile-only — md+ 에서는 outer aside 가 항상 노출되니까 무관.
  const [convListOpen, setConvListOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  // Optimistic in-flight user turn — renders immediately so the user sees
  // their message while the LLM (often Ollama 7.8b, 1–3 min) generates.
  const [pendingQuery, setPendingQuery] = useState<string | null>(null);

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
    setPendingQuery(content);
    try {
      // Pass current selectedKbIds so mid-conversation KB toggling actually
      // routes — backend prefers per-message kb_ids over the conversation row.
      await send.mutateAsync({
        content,
        force_mode: forceMode === "auto" ? null : forceMode,
        kb_ids: selectedKbIds,
      });
    } finally {
      // Server messages query invalidates on success; clear the optimistic
      // turn so we don't briefly render it twice.
      setPendingQuery(null);
    }
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
    // min-h-0 + h-full so the 3-pane respects its parent flex height (#4)
    <div className="flex h-full min-h-0 w-full">
      <ConversationSidebar
        activeId={activeId}
        onSelect={setActive}
        userEmail={userEmail}
        mobileOpen={convListOpen}
        onMobileClose={() => setConvListOpen(false)}
      />

      <main className="flex min-h-0 flex-1 flex-col">
        {/* Conversation header — KB scope 가 input 옆이 아니라 main 상단에서
          * 항상 보이도록 (B2). 사용자가 "지금 이 답변이 어떤 KB 기반"을 즉시
          * 인지 가능. ModeForceMenu 는 input 옆으로 collapse 유지 (per-message
          * mode 는 transient 컨트롤). */}
        <div className="flex h-12 shrink-0 items-center gap-3 border-b border-border-default bg-bg-canvas px-4 md:px-6">
          {/* mobile only — 대화 목록 drawer trigger. md+ 는 outer ConversationSidebar 가 항상 표시 */}
          <button
            type="button"
            onClick={() => setConvListOpen(true)}
            aria-label="대화 목록 열기"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-muted md:hidden"
          >
            <MessagesSquare size={18} strokeWidth={1.75} aria-hidden />
          </button>
          <span className="text-xs uppercase tracking-wider text-fg-subtle">맥락</span>
          <KbSelector />
        </div>

        {showOwnerOnboarding && (
          <div className="flex items-start gap-2 border-b border-border-default bg-bg-info px-4 py-2 text-sm">
            <Lightbulb aria-hidden size={16} strokeWidth={1.75} className="mt-0.5 shrink-0 text-accent-emphasis" />
            <span>오너 검색은 이제 채팅창에서 <code className="rounded bg-bg-muted px-1 py-0.5 font-mono text-xs">/owner 이름</code> 으로 가능합니다.</span>
          </div>
        )}

        <h1 className="sr-only">지식 검색</h1>
        <section className="flex-1 overflow-y-auto px-6 py-4">
          {messages.length === 0 && !send.isPending ? (
            <div className="mx-auto max-w-2xl space-y-8 py-12">
              {/* Hero — empty state 첫 진입 사용자 onboarding.
                * "차분하고 의도적" 톤은 유지하면서 product 정체성 명확히. */}
              <div className="space-y-3">
                <h2 className="text-2xl font-semibold tracking-tight text-fg-default">
                  무엇을 도와드릴까요?
                </h2>
                <p className="text-sm leading-relaxed text-fg-muted">
                  사내 운영 문서 / 매뉴얼 / 가이드를 검색해서 답변을 정리해 드립니다.
                  자연스러운 한국어로 질문하세요. 단축키{" "}
                  <kbd className="rounded border border-border-default bg-bg-muted px-1.5 py-0.5 font-mono text-xs">
                    ⌘/Ctrl + N
                  </kbd>{" "}
                  으로 새 대화를 시작할 수 있습니다.
                </p>
              </div>
              <RecommendedQueries
                onPick={(q) => handleSubmit(q)}
                pending={send.isPending}
              />
            </div>
          ) : (
            <ChatMessages
              messages={messages}
              pendingQuery={send.isPending ? pendingQuery : null}
              onMarkerActivate={setHighlightMarker}
              onMarkerDeactivate={() => setHighlightMarker(null)}
              onReportError={() => {/* handled inline by 호버 액션; PR4+ may add modal */}}
              onResubmit={(prior) => prior && handleSubmit(prior)}
              onFindOwner={() => setOwnerHint(true)}
            />
          )}
        </section>

        <footer className="border-t border-border-default px-6 py-3">
          {/* mobile/tablet (xl 미만) — 출처/메타 시트 trigger. 답변 chunks 가 있을 때만 표시. */}
          {sourceChunks.length > 0 && (
            <button
              type="button"
              onClick={() => setSourcesOpen(true)}
              className="mb-2 inline-flex items-center gap-1.5 rounded-md border border-border-default px-3 py-1.5 text-xs text-fg-muted transition-colors hover:bg-bg-muted xl:hidden"
              aria-label={`출처 ${sourceChunks.length}개 보기`}
            >
              <Paperclip aria-hidden size={12} strokeWidth={1.75} />
              <span>출처 ({sourceChunks.length})</span>
            </button>
          )}
          {ownerHint && (
            <p className="mb-1 flex items-center gap-1.5 text-xs text-fg-muted">
              <Lightbulb aria-hidden size={12} strokeWidth={1.75} className="shrink-0" />
              <span>입력창에 <code className="rounded bg-bg-muted px-1 py-0.5 font-mono text-xs">/owner 이름</code> 으로 오너를 검색할 수 있습니다.</span>
              <button onClick={() => setOwnerHint(false)} className="ml-2 underline hover:text-fg-default">
                닫기
              </button>
            </p>
          )}
          {/* ModeForceMenu 만 input 옆 — KbSelector 는 main 상단 conversation
            * header 로 승격됨 (B2). force_mode 는 한 메시지당 transient 컨트롤
            * 이라 input 근처가 자연. */}
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <ModeForceMenu value={forceMode} onChange={setForceMode} />
          </div>
          <ChatInput onSubmit={handleSubmit} pending={send.isPending} />
        </footer>
      </main>

      <SourcePanel
        chunks={sourceChunks}
        meta={(lastAssistant?.meta ?? {}) as Record<string, unknown>}
        highlightedMarker={highlightMarker}
        mobileOpen={sourcesOpen}
        onMobileClose={() => setSourcesOpen(false)}
      />
    </div>
  );
}
