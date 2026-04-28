"use client";

import { useEffect, useState } from "react";

import type { ChatMessage } from "@/lib/api/chat";

import { CitationMarker } from "./CitationMarker";
import { MessageActions } from "./MessageActions";

function renderWithCitations(
  text: string,
  onActivate: (n: number) => void,
  onDeactivate: () => void,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push(
      <CitationMarker
        key={`${m.index}-${m[1]}`}
        n={Number(m[1])}
        onActivate={onActivate}
        onDeactivate={onDeactivate}
      />,
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function firstCitedMarker(text: string): number | null {
  const m = /\[(\d+)\]/.exec(text);
  return m ? Number(m[1]) : null;
}

/** Pending assistant skeleton with an elapsed-time tick. Used while waiting
 * on a slow LLM (Ollama 7.8b commonly takes 1–3 min for Korean RAG answers).
 * The hint copy escalates so the user knows we haven't silently failed. */
function PendingAssistant() {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  let hint = "답변을 생성하고 있어요…";
  if (elapsed >= 120) hint = "거의 다 됐어요 — Ollama가 한국어 답변을 정리하는 중입니다.";
  else if (elapsed >= 60) hint = "오래 걸리고 있어요 — 보통 1~3분이 정상입니다. 조금만 기다려 주세요.";
  else if (elapsed >= 20) hint = "관련 문서를 찾았어요 — 답변을 정리 중입니다.";
  else if (elapsed >= 5) hint = "지식베이스에서 관련 문서를 찾는 중…";

  const mm = String(Math.floor(elapsed / 60)).padStart(1, "0");
  const ss = String(elapsed % 60).padStart(2, "0");

  return (
    <li className="space-y-2" aria-live="polite" aria-busy="true">
      <p className="text-xs uppercase text-fg-subtle">assistant</p>
      <div className="flex items-center gap-2 text-sm text-fg-muted">
        <span className="inline-flex h-2 w-2 animate-pulse rounded-full bg-fg-default" aria-hidden />
        <span>{hint}</span>
        <span className="ml-2 font-mono text-xs text-fg-subtle">{mm}:{ss}</span>
      </div>
      <div className="space-y-2 pt-1">
        <div className="h-3 w-3/4 animate-pulse rounded bg-bg-emphasis" />
        <div className="h-3 w-5/6 animate-pulse rounded bg-bg-emphasis" />
        <div className="h-3 w-2/3 animate-pulse rounded bg-bg-emphasis" />
      </div>
    </li>
  );
}

export function ChatMessages({
  messages,
  pendingQuery,
  onMarkerActivate,
  onMarkerDeactivate,
  onReportError,
  onResubmit,
  onFindOwner,
}: {
  messages: ChatMessage[];
  /** When non-null, render an optimistic user bubble with this content plus
   * an assistant skeleton. Cleared by ChatPage once the mutation resolves. */
  pendingQuery?: string | null;
  onMarkerActivate: (n: number) => void;
  onMarkerDeactivate: () => void;
  onReportError: () => void;
  onResubmit: (priorUserContent: string) => void;
  onFindOwner: () => void;
}) {
  function priorUserOf(idx: number): string {
    for (let i = idx - 1; i >= 0; i--) {
      if (messages[i].role === "user") return messages[i].content;
    }
    return "";
  }
  return (
    <ul className="space-y-4">
      {messages.map((m, idx) => (
        <li key={m.id} className="group">
          <p className="text-xs uppercase text-fg-subtle">{m.role}</p>
          <div className="mt-1 whitespace-pre-wrap text-sm">
            {renderWithCitations(m.content, onMarkerActivate, onMarkerDeactivate)}
          </div>
          {m.role === "assistant" && (
            <MessageActions
              content={m.content}
              onShowSources={() => {
                const first = firstCitedMarker(m.content);
                if (first !== null) onMarkerActivate(first);
                else onMarkerDeactivate();
              }}
              onFindOwner={onFindOwner}
              onResubmit={() => onResubmit(priorUserOf(idx))}
              onReportError={onReportError}
            />
          )}
        </li>
      ))}
      {pendingQuery && (
        <>
          <li>
            <p className="text-xs uppercase text-fg-subtle">user</p>
            <div className="mt-1 whitespace-pre-wrap text-sm">{pendingQuery}</div>
          </li>
          <PendingAssistant />
        </>
      )}
    </ul>
  );
}
