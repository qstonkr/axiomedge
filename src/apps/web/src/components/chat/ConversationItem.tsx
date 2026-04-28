"use client";

import { useState } from "react";

import { cn } from "@/components/ui/cn";
import { useDeleteConversation, useRenameConversation } from "@/store/conversations";

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
        "group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm",
        active ? "bg-bg-emphasis" : "hover:bg-bg-muted",
      )}
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
          onClick={() => onSelect(id)}
          className="flex-1 truncate text-left"
        >
          {title || "(제목 없음)"}
        </button>
      )}
      <button
        aria-label="이름 변경"
        onClick={(e) => {
          e.stopPropagation();
          setEditing(true);
        }}
        className="opacity-0 group-hover:opacity-100"
      >
        ✏️
      </button>
      <button
        aria-label="삭제"
        onClick={async (e) => {
          e.stopPropagation();
          if (confirm("이 대화를 삭제할까요?")) await remove.mutateAsync(id);
        }}
        className="opacity-0 group-hover:opacity-100"
      >
        🗑️
      </button>
    </div>
  );
}
