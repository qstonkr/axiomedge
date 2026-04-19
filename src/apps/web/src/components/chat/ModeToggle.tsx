"use client";

import { cn } from "@/components/ui";
import { useChatStore, type ChatMode } from "@/store/chat";

const MODES: { id: ChatMode; label: string; hint: string }[] = [
  { id: "agentic", label: "AI 답변", hint: "plan → 검색 → 답변 (~5–10s)" },
  { id: "fast", label: "빠른 검색", hint: "chunks 만 (<1s)" },
];

export function ModeToggle() {
  const mode = useChatStore((s) => s.mode);
  const setMode = useChatStore((s) => s.setMode);

  return (
    <div role="radiogroup" aria-label="검색 모드" className="inline-flex rounded-md border border-border-default bg-bg-canvas p-0.5">
      {MODES.map((m) => {
        const active = mode === m.id;
        return (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={active}
            title={m.hint}
            onClick={() => setMode(m.id)}
            className={cn(
              "rounded px-3 py-1 text-xs font-medium transition-colors",
              active
                ? "bg-accent-default text-fg-onAccent"
                : "text-fg-muted hover:bg-bg-muted hover:text-fg-default",
            )}
          >
            {m.label}
          </button>
        );
      })}
    </div>
  );
}
