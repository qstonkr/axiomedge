"use client";

import { useState } from "react";

import { parseSlash, SlashCommandDropdown } from "./SlashCommands";

export function ChatInput({
  onSubmit,
  pending,
}: {
  onSubmit: (content: string) => void | Promise<void>;
  pending: boolean;
}) {
  const [value, setValue] = useState("");
  const slash = parseSlash(value);
  const showDropdown = slash !== null && slash.arg === "";

  function submit() {
    const v = value.trim();
    if (!v || pending) return;
    onSubmit(v);
    setValue("");
  }

  return (
    <div className="relative">
      {showDropdown && (
        <SlashCommandDropdown
          query={slash!.cmd}
          onPick={(name) => setValue(`/${name} `)}
        />
      )}
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submit();
          }
        }}
        rows={2}
        aria-label="질문 입력"
        placeholder="질문을 입력하세요. ⌘/Ctrl+Enter 전송. /owner 같은 명령도 가능."
        className="w-full resize-none rounded-md border border-border-default bg-bg-canvas px-3 py-2 text-sm focus-visible:border-accent-default focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-default disabled:opacity-60"
        disabled={pending}
      />
      <p className="mt-1 text-right text-xs text-fg-subtle">
        ⌘/Ctrl + Enter 전송
      </p>
    </div>
  );
}
