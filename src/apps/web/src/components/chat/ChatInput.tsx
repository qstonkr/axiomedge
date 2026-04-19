"use client";

import { useRef, type FormEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/ui";

export function ChatInput({
  onSubmit,
  pending,
  placeholder = "예: 신촌점 차주 매장 점검 일정 알려줘",
}: {
  onSubmit: (query: string) => void;
  pending: boolean;
  placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  function send() {
    const q = ref.current?.value.trim() ?? "";
    if (!q) return;
    onSubmit(q);
    if (ref.current) ref.current.value = "";
  }

  function onFormSubmit(e: FormEvent) {
    e.preventDefault();
    send();
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    // ⌘/Ctrl+Enter — submit. plain Enter inserts newline (multi-line OK).
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      send();
    }
  }

  return (
    <form
      onSubmit={onFormSubmit}
      className="flex items-end gap-2 rounded-lg border border-border-default bg-bg-canvas p-2 shadow-sm"
    >
      <textarea
        ref={ref}
        rows={2}
        placeholder={placeholder}
        onKeyDown={onKey}
        disabled={pending}
        aria-label="검색어"
        className="min-h-[48px] flex-1 resize-none border-0 bg-transparent px-2 py-1 text-sm text-fg-default placeholder:text-fg-subtle focus:outline-none disabled:opacity-50"
      />
      <Button type="submit" disabled={pending} size="sm">
        {pending ? "검색 중…" : "검색"}
      </Button>
    </form>
  );
}
