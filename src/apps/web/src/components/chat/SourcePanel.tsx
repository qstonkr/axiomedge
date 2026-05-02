"use client";

import { useEffect, useState } from "react";
import { Activity, Paperclip, User, X } from "lucide-react";

import { cn } from "@/components/ui/cn";

import { MetaSignals } from "./MetaSignals";
import type { AssistantTurn } from "./types";

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
  /** xl 미만 화면에서 bottom sheet 로 열림 (B3 follow-up). desktop xl+ 에는
   * aside 가 영구 표시되니 무시. */
  mobileOpen?: boolean;
  onMobileClose?: () => void;
};

export function SourcePanel({
  chunks,
  meta,
  highlightedMarker,
  mobileOpen = false,
  onMobileClose,
}: Props) {
  const [tab, setTab] = useState<"sources" | "meta">("sources");

  // mobile bottom sheet — Esc + body scroll lock
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onMobileClose?.();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [mobileOpen, onMobileClose]);

  const tabs = (
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
        <span className="inline-flex items-center justify-center gap-1.5">
          <Paperclip aria-hidden size={14} strokeWidth={1.75} />
          출처 {chunks.length > 0 && `(${chunks.length})`}
        </span>
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
        <span className="inline-flex items-center justify-center gap-1.5">
          <Activity aria-hidden size={14} strokeWidth={1.75} />
          메타
        </span>
      </button>
    </div>
  );

  const body = (
    <div className="flex-1 overflow-y-auto p-3">
      {tab === "sources" && (chunks.length === 0 ? (
        <p className="text-sm text-fg-default">출처가 없습니다.</p>
      ) : (
        chunks.map((c) => (
          <article
            key={c.chunk_id}
            data-marker={c.marker ?? ""}
            data-highlighted={c.marker === highlightedMarker ? "true" : "false"}
            className={cn(
              "mb-3 rounded-md border border-border-default bg-bg-default p-3 transition-colors",
              c.marker === highlightedMarker
                ? "border-accent-default ring-2 ring-accent-default"
                : "hover:border-border-strong",
            )}
          >
            <h4 className="text-sm font-medium">
              {c.marker != null && (
                <span className="mr-1 font-semibold text-accent-emphasis">[{c.marker}]</span>
              )}
              {c.doc_title}
            </h4>
            <p className="mt-1 flex items-center gap-1 text-xs text-fg-muted">
              <span>{c.kb_id}</span>
              {c.owner && (
                <>
                  <span aria-hidden>·</span>
                  <User aria-hidden size={11} strokeWidth={1.75} />
                  <span>{c.owner}</span>
                </>
              )}
            </p>
            <p className="mt-2 line-clamp-3 text-xs text-fg-default">{c.snippet}</p>
            {typeof c.score === "number" && (
              <p className="mt-2 text-xs text-fg-muted">
                신뢰도 <span className="font-medium text-fg-default">{(c.score * 100).toFixed(0)}%</span>
              </p>
            )}
          </article>
        ))
      ))}
      {tab === "meta" && (
        <MetaSignals meta={meta as AssistantTurn["meta"]} />
      )}
    </div>
  );

  return (
    <>
      {/* desktop xl+ — 영구 right panel */}
      <aside
        aria-label="출처 및 메타"
        className="hidden w-[360px] shrink-0 self-stretch border-l border-border-default bg-bg-subtle xl:flex xl:flex-col"
      >
        {tabs}
        {body}
      </aside>

      {/* mobile/tablet (xl 미만) — bottom sheet (B3 follow-up). 부모가 trigger 로 토글 */}
      {mobileOpen && (
        <>
          <div
            aria-hidden
            onClick={onMobileClose}
            className="fixed inset-0 z-40 bg-fg-default/40 backdrop-blur-sm xl:hidden"
          />
          <aside
            role="dialog"
            aria-label="출처 및 메타"
            aria-modal="true"
            className="fixed inset-x-0 bottom-0 z-50 flex max-h-[85vh] flex-col rounded-t-xl border-t border-border-default bg-bg-subtle shadow-lg xl:hidden"
          >
            <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
              <span className="text-sm font-medium text-fg-default">출처 / 메타</span>
              <button
                type="button"
                onClick={onMobileClose}
                aria-label="닫기"
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-muted"
              >
                <X size={18} strokeWidth={1.75} aria-hidden />
              </button>
            </div>
            {tabs}
            {body}
          </aside>
        </>
      )}
    </>
  );
}
