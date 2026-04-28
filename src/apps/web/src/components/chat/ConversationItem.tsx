"use client";

import { useState } from "react";

import { cn } from "@/components/ui/cn";
import { useDeleteConversation, useRenameConversation } from "@/store/conversations";

const ICON_BTN =
  "rounded px-1 py-0.5 text-fg-muted opacity-60 transition hover:bg-bg-muted hover:text-fg-default hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-accent-default";

export function ConversationItem({
  id, title, active, onSelect,
}: {
  id: string;
  title: string;
  active: boolean;
  onSelect: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const rename = useRenameConversation();
  const remove = useDeleteConversation();

  async function commitRename() {
    setEditing(false);
    if (draft.trim() && draft !== title) {
      await rename.mutateAsync({ id, title: draft.trim() });
    } else {
      setDraft(title);
    }
  }

  return (
    <div
      className={cn(
        "group relative flex items-center gap-1 rounded-md pl-3 pr-2 py-1.5 text-sm transition-colors",
        active
          // Active row: accent-tinted background + left bar so the selected
          // conversation is easy to scan, not just slightly grayer.
          ? "bg-accent-subtle font-medium text-fg-default before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-0.5 before:rounded before:bg-accent-default"
          : "hover:bg-bg-muted",
      )}
      aria-current={active ? "page" : undefined}
    >
      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitRename();
            if (e.key === "Escape") {
              setEditing(false);
              setDraft(title);
            }
          }}
          className="flex-1 bg-transparent outline-none"
        />
      ) : (
        <button
          type="button"
          onClick={() => onSelect(id)}
          className="flex-1 truncate text-left"
        >
          {title || "(제목 없음)"}
        </button>
      )}
      {/* Always-visible icons (muted by default, full on hover/focus) — was
          opacity-0 hover-only which broke touch + keyboard discoverability. */}
      <button
        type="button"
        aria-label="이름 변경"
        onClick={(e) => {
          e.stopPropagation();
          setEditing(true);
        }}
        className={ICON_BTN}
      >
        ✏️
      </button>
      <button
        type="button"
        aria-label="삭제"
        onClick={async (e) => {
          e.stopPropagation();
          if (confirm("이 대화를 삭제할까요?")) await remove.mutateAsync(id);
        }}
        className={ICON_BTN}
      >
        🗑️
      </button>
    </div>
  );
}
