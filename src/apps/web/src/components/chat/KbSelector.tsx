"use client";

import { useState } from "react";

import { Badge } from "@/components/ui";
import { useSearchableKbs } from "@/hooks/useSearch";
import { useChatStore } from "@/store/chat";

/**
 * KB scope selector — popover with checkbox list. Empty selection = all
 * accessible KBs (FastAPI then falls back to org-scoped active set).
 */
export function KbSelector() {
  const { data: kbs, isLoading } = useSearchableKbs();
  const selected = useChatStore((s) => s.selectedKbIds);
  const toggle = useChatStore((s) => s.toggleKb);
  const setSelected = useChatStore((s) => s.setSelectedKbIds);
  const [open, setOpen] = useState(false);

  const total = kbs?.length ?? 0;
  const label =
    selected.length === 0 ? `전체 KB (${total})` : `KB ${selected.length}개 선택`;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 rounded-md border border-border-default bg-bg-canvas px-3 py-1.5 text-xs text-fg-default transition-colors hover:bg-bg-muted"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span>{label}</span>
        <span aria-hidden className="text-fg-subtle">▾</span>
      </button>
      {open && (
        <div className="absolute left-0 z-20 mt-2 w-72 rounded-lg border border-border-default bg-bg-canvas p-2 shadow-md">
          <div className="flex items-center justify-between px-2 pb-2">
            <span className="text-xs text-fg-muted">KB 선택</span>
            <button
              type="button"
              onClick={() => setSelected([])}
              className="text-xs text-fg-muted hover:text-fg-default"
            >
              전체 해제
            </button>
          </div>
          <ul role="listbox" className="max-h-64 space-y-0.5 overflow-y-auto">
            {isLoading && (
              <li className="px-2 py-1 text-xs text-fg-subtle">불러오는 중…</li>
            )}
            {!isLoading && (kbs ?? []).map((kb) => {
              const checked = selected.includes(kb.kb_id);
              return (
                <li key={kb.kb_id}>
                  <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-xs hover:bg-bg-muted">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(kb.kb_id)}
                      className="h-3.5 w-3.5 accent-accent-default"
                    />
                    <span className="line-clamp-1 flex-1 text-fg-default">
                      {kb.name}
                    </span>
                    {kb.tier && (
                      <Badge tone={kb.tier === "personal" ? "warning" : "neutral"}>
                        {kb.tier}
                      </Badge>
                    )}
                  </label>
                </li>
              );
            })}
            {!isLoading && total === 0 && (
              <li className="px-2 py-1 text-xs text-fg-subtle">
                접근 가능한 KB 가 없습니다.
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
