"use client";

import { useState } from "react";

import { cn } from "@/components/ui/cn";

export type SourceChunk = {
  chunk_id: string;
  marker?: number;
  doc_title: string;
  kb_id: string;
  snippet: string;
  score?: number;
  owner?: string | null;
};

type Props = {
  chunks: SourceChunk[];
  meta: Record<string, unknown>;
  highlightedMarker: number | null;
};

export function SourcePanel({ chunks, meta, highlightedMarker }: Props) {
  const [tab, setTab] = useState<"sources" | "meta">("sources");

  return (
    <aside className="hidden w-[360px] shrink-0 self-stretch border-l border-border-default bg-bg-subtle xl:flex xl:flex-col">
      <div role="tablist" className="flex border-b border-border-default">
        <button
          role="tab"
          aria-selected={tab === "sources"}
          onClick={() => setTab("sources")}
          className={cn(
            "flex-1 px-3 py-2 text-sm",
            tab === "sources"
              ? "border-b-2 border-fg-default font-medium"
              : "text-fg-muted",
          )}
        >
          📎 출처 {chunks.length > 0 && `(${chunks.length})`}
        </button>
        <button
          role="tab"
          aria-selected={tab === "meta"}
          onClick={() => setTab("meta")}
          className={cn(
            "flex-1 px-3 py-2 text-sm",
            tab === "meta"
              ? "border-b-2 border-fg-default font-medium"
              : "text-fg-muted",
          )}
        >
          🧪 메타
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {tab === "sources" && (chunks.length === 0 ? (
          <p className="text-xs text-fg-muted">출처가 없습니다.</p>
        ) : (
          chunks.map((c) => (
            <article
              key={c.chunk_id}
              data-marker={c.marker ?? ""}
              data-highlighted={c.marker === highlightedMarker ? "true" : "false"}
              className={cn(
                "mb-3 rounded-md border border-border-default bg-bg-default p-3 transition-colors",
                c.marker === highlightedMarker && "ring-2 ring-fg-default",
              )}
            >
              <h4 className="text-sm font-medium">
                {c.marker != null && (
                  <span className="mr-1 text-fg-subtle">[{c.marker}]</span>
                )}
                {c.doc_title}
              </h4>
              <p className="mt-1 text-xs text-fg-muted">
                {c.kb_id}
                {c.owner && ` · 👤 ${c.owner}`}
              </p>
              <p className="mt-2 line-clamp-3 text-xs">{c.snippet}</p>
              {typeof c.score === "number" && (
                <p className="mt-1 text-[10px] text-fg-subtle">
                  신뢰도 {(c.score * 100).toFixed(0)}%
                </p>
              )}
            </article>
          ))
        ))}
        {tab === "meta" && (
          <pre className="text-xs whitespace-pre-wrap break-all">
            {JSON.stringify(meta, null, 2)}
          </pre>
        )}
      </div>
    </aside>
  );
}
