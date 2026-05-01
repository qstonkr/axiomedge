"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";

import { Dialog, Input } from "@/components/ui";

import { ADMIN_NAV } from "./nav";

/**
 * 가벼운 command palette (Cmd/Ctrl+K) — admin 라우트로 빠른 이동.
 * NAV 는 ./nav.ts SSOT 사용 (sidebar / mobile 과 공유).
 */
const CMDS = ADMIN_NAV;

export function AdminQuickPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);

  // ⌘K / Ctrl+K — 토글
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const isToggle = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isToggle) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return CMDS;
    return CMDS.filter((c) =>
      (c.label + " " + c.group + " " + c.href).toLowerCase().includes(query),
    );
  }, [q]);

  function onListKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(filtered.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const c = filtered[active];
      if (c) {
        router.push(c.href);
        setOpen(false);
        setQ("");
      }
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="명령 팔레트 열기 (⌘K)"
        title="⌘K"
        className="flex items-center gap-2 rounded-md border border-border-default bg-bg-canvas px-2.5 py-1 text-xs text-fg-muted transition-colors hover:bg-bg-muted hover:text-fg-default"
      >
        <Search aria-hidden size={12} strokeWidth={1.75} />
        <span className="hidden sm:inline">빠른 이동…</span>
        <kbd className="hidden rounded border border-border-default bg-bg-subtle px-1 font-mono text-[10px] sm:inline">
          ⌘K
        </kbd>
      </button>

      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
          setQ("");
          setActive(0);
        }}
        title="빠른 이동"
        description="화살표로 선택, Enter 로 이동, Esc 로 닫기"
        width="lg"
      >
        <div className="space-y-3" onKeyDown={onListKey}>
          <Input
            autoFocus
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setActive(0);
            }}
            placeholder="페이지 이름 / 그룹 / URL"
          />
          <ul className="max-h-80 overflow-y-auto" role="listbox">
            {filtered.length === 0 ? (
              <li className="px-3 py-6 text-center text-xs text-fg-muted">
                결과 없음
              </li>
            ) : (
              filtered.map((c, idx) => {
                const selected = idx === active;
                const Icon = c.Icon;
                return (
                  <li key={c.href}>
                    <Link
                      href={c.href}
                      role="option"
                      aria-selected={selected}
                      onClick={() => {
                        setOpen(false);
                        setQ("");
                      }}
                      onMouseEnter={() => setActive(idx)}
                      className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm ${
                        selected
                          ? "bg-accent-subtle text-fg-default"
                          : "text-fg-default hover:bg-bg-muted"
                      }`}
                    >
                      <Icon aria-hidden size={14} strokeWidth={1.75} className="shrink-0 text-fg-muted" />
                      <span className="flex-1 truncate">{c.label}</span>
                      <span className="font-mono text-[10px] text-fg-subtle">
                        {c.group}
                      </span>
                      <span className="font-mono text-[10px] text-fg-subtle">
                        {c.href}
                      </span>
                    </Link>
                  </li>
                );
              })
            )}
          </ul>
        </div>
      </Dialog>
    </>
  );
}
