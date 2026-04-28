"use client";

import { useState } from "react";

export type ForceMode = "auto" | "quick" | "deep";

const LABELS: Record<ForceMode, string> = {
  auto: "자동",
  quick: "빠른 검색",
  deep: "심층 검색",
};

export function ModeForceMenu({
  value, onChange,
}: {
  value: ForceMode;
  onChange: (v: ForceMode) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="rounded-md border border-border-default px-2 py-1 text-xs"
        title="고급 — 검색 모드 강제"
      >
        ⚙️ 고급 · {LABELS[value]}
      </button>
      {open && (
        <ul
          role="menu"
          className="absolute right-0 top-full z-10 mt-1 w-40 overflow-hidden rounded-md border border-border-default bg-bg-default shadow-lg"
        >
          {(Object.keys(LABELS) as ForceMode[]).map((k) => (
            <li key={k}>
              <button
                role="menuitem"
                onClick={() => {
                  onChange(k);
                  setOpen(false);
                }}
                className="block w-full px-3 py-2 text-left text-sm hover:bg-bg-muted"
              >
                {LABELS[k]}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
