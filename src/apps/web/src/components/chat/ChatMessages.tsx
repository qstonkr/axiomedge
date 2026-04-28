"use client";

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

export function ChatMessages({
  messages,
  onMarkerActivate,
  onMarkerDeactivate,
  onReportError,
  onResubmit,
  onFindOwner,
}: {
  messages: ChatMessage[];
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
                // Activate the first cited marker if any; otherwise just open
                // the panel without forcing a specific highlight.
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
    </ul>
  );
}
