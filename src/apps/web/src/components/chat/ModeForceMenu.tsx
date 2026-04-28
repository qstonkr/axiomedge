"use client";

import { useEffect, useRef, useState } from "react";

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
  const wrapRef = useRef<HTMLDivElement>(null);

  // Close on outside click + Escape — matches the rest of our popovers
  // (KbSelector, ProfileDropdown).
  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="rounded-md border border-border-default px-2 py-1.5 text-xs hover:bg-bg-muted focus-visible:outline-2 focus-visible:outline-accent-default"
        title="고급 — 검색 모드 강제"
      >
        ⚙️ 고급 · {LABELS[value]}
      </button>
      {open && (
        <ul
          role="menu"
          className="absolute right-0 top-full z-10 mt-1 w-44 overflow-hidden rounded-md border border-border-default bg-bg-canvas shadow-md"
        >
          {(Object.keys(LABELS) as ForceMode[]).map((k) => (
            <li key={k}>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  onChange(k);
                  setOpen(false);
                }}
                className="block w-full min-h-[36px] px-3 py-2 text-left text-sm hover:bg-bg-muted focus-visible:bg-bg-muted focus-visible:outline-none"
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
