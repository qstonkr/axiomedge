"use client";

import { useState } from "react";

import { Button, EmptyState, useToast } from "@/components/ui";
import { useAgenticAsk, useHubSearch } from "@/hooks/useSearch";
import { useChatStore } from "@/store/chat";

import { ChatInput } from "./ChatInput";
import { ChatMessages } from "./ChatMessages";
import { ErrorReportDialog } from "./ErrorReportDialog";
import { KbSelector } from "./KbSelector";
import { ModeToggle } from "./ModeToggle";
import { RecommendedQueries } from "./RecommendedQueries";
import type { AssistantTurn, ChunkSource, UserTurn } from "./types";

// id collision 방지 — sessionStorage 복원 후 다시 카운트 시작해도 충돌 없도록
// timestamp + random tail 사용 (TURN_ID counter 는 module 재실행 시 0 으로 리셋됨).
const newId = (prefix: string): string =>
  `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

export function ChatPage() {
  const turns = useChatStore((s) => s.turns);
  const appendTurn = useChatStore((s) => s.appendTurn);
  const clearTurns = useChatStore((s) => s.clearTurns);
  const [pendingQuery, setPendingQuery] = useState<string | null>(null);
  const [reportTarget, setReportTarget] = useState<ChunkSource | null>(null);

  const selectedKbIds = useChatStore((s) => s.selectedKbIds);
  const mode = useChatStore((s) => s.mode);

  const search = useHubSearch();
  const agentic = useAgenticAsk();
  const toast = useToast();

  const pending = search.isPending || agentic.isPending;

  async function onSubmit(query: string) {
    const userTurn: UserTurn = { kind: "user", id: newId("u"), query };
    appendTurn(userTurn);
    setPendingQuery(query);

    try {
      let assistant: AssistantTurn;
      if (mode === "agentic") {
        const res = await agentic.mutateAsync({
          query,
          kb_ids: selectedKbIds.length > 0 ? selectedKbIds : null,
        });
        assistant = {
          kind: "assistant",
          id: newId("a"),
          query,
          answer: res.answer,
          chunks: [], // agentic 은 separate trace 에 있고, /chat 1차 화면에는 답변만
          failure_reason: res.failure_reason ?? null,
          errors: res.errors ?? [],
          meta: {
            confidence: res.confidence,
            iteration_count: res.iteration_count,
            estimated_cost_usd: res.estimated_cost_usd,
            llm_provider: res.llm_provider,
            trace_id: res.trace_id,
          },
        };
      } else {
        const res = await search.mutateAsync({
          query,
          kb_ids: selectedKbIds.length > 0 ? selectedKbIds : null,
          top_k: 8,
          include_answer: true,
        });
        const md = res.metadata ?? {};
        assistant = {
          kind: "assistant",
          id: newId("a"),
          query,
          answer: res.answer ?? "",
          chunks: (res.chunks ?? []) as ChunkSource[],
          searched_kbs: res.searched_kbs,
          meta: {
            confidence: res.confidence,
            confidence_level: (md.confidence_level as string | undefined) ?? undefined,
            crag_action: (md.crag_action as string | undefined) ?? null,
            query_type: res.query_type,
            search_time_ms: res.search_time_ms,
            rerank_breakdown:
              (md.rerank_breakdown as
                | { dense?: number; sparse?: number; colbert?: number; cross_encoder?: number }
                | undefined) ??
              (md.composite_rerank as
                | { dense?: number; sparse?: number; colbert?: number; cross_encoder?: number }
                | undefined),
            expanded_terms:
              (md.expanded_terms as string[] | undefined) ??
              (md.query_expansion as string[] | undefined),
            corrected_query: md.corrected_query as string | undefined,
            original_query: md.original_query as string | undefined,
            working_memory_hit:
              (md.working_memory_hit as boolean | undefined) ??
              (md.wm_probe_hit as boolean | undefined),
          },
        };
      }
      appendTurn(assistant);
    } catch (err) {
      const detail = err instanceof Error ? err.message : "검색에 실패했습니다.";
      toast.push(detail, "danger");
    } finally {
      setPendingQuery(null);
    }
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-4xl flex-col gap-4 px-6 py-8">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold leading-snug text-fg-default">
            💬 지식 검색
          </h1>
          {turns.length > 0 && (
            <Button
              size="sm"
              variant="ghost"
              onClick={clearTurns}
              title="이 탭의 대화 기록을 지웁니다 (sessionStorage)"
            >
              🗑️ 대화 지우기
            </Button>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <KbSelector />
          <ModeToggle />
        </div>
      </header>

      <section className="flex-1 overflow-y-auto pb-4">
        {turns.length === 0 && !pending ? (
          <div className="space-y-4">
            <EmptyState
              icon="🔍"
              title="궁금한 것을 물어보세요"
              description="신촌점 차주 매장 점검 일정 / PBU 와 관련된 시스템 / 오늘 휴무 매장…"
            />
            <RecommendedQueries onPick={(q) => onSubmit(q)} pending={pending} />
          </div>
        ) : (
          <>
            <ChatMessages
              turns={turns}
              pending={pending}
              pendingQuery={pendingQuery}
              onReportError={setReportTarget}
            />
            {turns.length > 0 && !pending && (
              <details className="mt-4 rounded-md border border-border-default bg-bg-subtle px-3 py-2 text-xs">
                <summary className="cursor-pointer text-fg-muted">
                  💡 추천 질문 더 보기
                </summary>
                <div className="mt-2">
                  <RecommendedQueries onPick={(q) => onSubmit(q)} pending={pending} />
                </div>
              </details>
            )}
          </>
        )}
      </section>

      <footer className="border-t border-border-default pt-4">
        <ChatInput onSubmit={onSubmit} pending={pending} />
        <p className="mt-2 text-xs text-fg-subtle">
          ⌘/Ctrl + Enter 로 전송 · {mode === "agentic" ? "AI 답변 모드 — 5–10초" : "빠른 검색 — chunk 만"}
        </p>
      </footer>

      {reportTarget && (
        <ErrorReportDialog
          chunk={reportTarget}
          onClose={() => setReportTarget(null)}
        />
      )}
    </div>
  );
}
