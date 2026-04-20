"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Dialog, Input } from "@/components/ui";

type Cmd = { href: string; label: string; group: string; icon: string };

/**
 * 가벼운 command palette (Cmd/Ctrl+K) — 17 admin 라우트로 빠른 이동.
 * 외부 의존성 없음. fuzzy match 는 단순 substring + 토큰 일치.
 */
const CMDS: Cmd[] = [
  { href: "/admin", label: "운영 대시보드", group: "개요", icon: "📊" },
  { href: "/admin/sources", label: "데이터 소스", group: "콘텐츠", icon: "🔌" },
  { href: "/admin/ingest", label: "Ingest 작업", group: "콘텐츠", icon: "⚙️" },
  { href: "/admin/glossary", label: "용어집", group: "콘텐츠", icon: "📖" },
  { href: "/admin/owners", label: "담당자 관리", group: "콘텐츠", icon: "👥" },
  { href: "/admin/groups", label: "검색 그룹", group: "콘텐츠", icon: "🗂️" },
  { href: "/admin/conflicts", label: "중복/모순", group: "콘텐츠", icon: "⚠️" },
  { href: "/admin/verification", label: "검증 대기", group: "콘텐츠", icon: "🔍" },
  { href: "/admin/lifecycle", label: "문서 라이프사이클", group: "콘텐츠", icon: "♻️" },
  { href: "/admin/quality", label: "RAG 품질", group: "품질", icon: "📈" },
  { href: "/admin/golden-set", label: "Golden Set", group: "품질", icon: "🥇" },
  { href: "/admin/traces", label: "Agent Trace", group: "품질", icon: "🛤️" },
  { href: "/admin/errors", label: "오류 신고", group: "품질", icon: "🚨" },
  { href: "/admin/users", label: "사용자/권한", group: "운영", icon: "🔐" },
  { href: "/admin/edge", label: "Edge 모델", group: "운영", icon: "🌐" },
  { href: "/admin/jobs", label: "작업 모니터", group: "운영", icon: "📋" },
  { href: "/admin/config", label: "가중치 설정", group: "운영", icon: "🎚️" },
  { href: "/admin/graph", label: "엔티티 탐색", group: "그래프", icon: "🕸️" },
];

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

  // 키보드 네비게이션
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
        <span aria-hidden>🔎</span>
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
                      <span aria-hidden className="text-base leading-none">
                        {c.icon}
                      </span>
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
