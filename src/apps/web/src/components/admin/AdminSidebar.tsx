"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/components/ui/cn";

type NavItem = { href: string; label: string; icon: string };
type NavGroup = { label: string; items: NavItem[] };

/**
 * Admin sidebar — 4 그룹 메뉴 (B-2 plan).
 * 운영자가 한눈에 모든 admin 영역을 볼 수 있도록 항상 펼친 상태.
 * dark bg 는 light/dark theme 와 무관하게 유지 (Sentry/Posthog 패턴).
 */
const NAV: NavGroup[] = [
  {
    label: "개요",
    items: [{ href: "/admin", label: "운영 대시보드", icon: "📊" }],
  },
  {
    label: "콘텐츠 관리",
    items: [
      { href: "/admin/sources", label: "데이터 소스", icon: "🔌" },
      { href: "/admin/ingest", label: "Ingest 작업", icon: "⚙️" },
      { href: "/admin/glossary", label: "용어집", icon: "📖" },
      { href: "/admin/owners", label: "담당자 관리", icon: "👥" },
      { href: "/admin/groups", label: "검색 그룹", icon: "🗂️" },
      { href: "/admin/conflicts", label: "중복/모순", icon: "⚠️" },
      { href: "/admin/verification", label: "검증 대기", icon: "🔍" },
      { href: "/admin/lifecycle", label: "문서 라이프사이클", icon: "♻️" },
    ],
  },
  {
    label: "품질·평가",
    items: [
      { href: "/admin/quality", label: "RAG 품질", icon: "📈" },
      { href: "/admin/golden-set", label: "Golden Set", icon: "🥇" },
      { href: "/admin/traces", label: "Agent Trace", icon: "🛤️" },
      { href: "/admin/errors", label: "오류 신고", icon: "🚨" },
    ],
  },
  {
    label: "시스템 운영",
    items: [
      { href: "/admin/users", label: "사용자/권한", icon: "🔐" },
      { href: "/admin/edge", label: "Edge 모델", icon: "🌐" },
      { href: "/admin/jobs", label: "작업 모니터", icon: "📋" },
      { href: "/admin/config", label: "가중치 설정", icon: "🎚️" },
    ],
  },
  {
    label: "그래프",
    items: [{ href: "/admin/graph", label: "엔티티 탐색", icon: "🕸️" }],
  },
];

export function AdminSidebar() {
  const pathname = usePathname();

  return (
    <aside
      className="hidden w-60 shrink-0 self-stretch overflow-y-auto bg-admin-sidebar-bg px-3 py-4 text-admin-sidebar-fg md:block"
      style={{ borderRight: "1px solid var(--color-admin-sidebar-border)" }}
    >
      <Link
        href="/admin"
        className="mb-5 flex items-center gap-2 px-3 text-sm font-semibold text-admin-sidebar-fg-strong"
      >
        axiomedge
        <span
          aria-label="관리자 영역"
          className="rounded bg-accent-default px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-fg-onAccent"
        >
          Admin
        </span>
      </Link>
      <nav className="space-y-5" aria-label="관리자 메뉴">
        {NAV.map((group, idx) => (
          <div key={group.label}>
            {idx > 0 && (
              <div
                aria-hidden
                className="mb-3 h-px"
                style={{
                  background: "var(--color-admin-sidebar-border)",
                }}
              />
            )}
            <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-wider text-admin-sidebar-fg/60">
              {group.label}
            </p>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active =
                  pathname === item.href ||
                  (item.href !== "/admin" && pathname.startsWith(`${item.href}/`));
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "relative flex items-center gap-2.5 rounded-md px-3 py-1.5 text-xs transition-colors",
                        active
                          ? "bg-admin-sidebar-active-bg text-admin-sidebar-active-fg font-medium"
                          : "text-admin-sidebar-fg hover:bg-admin-sidebar-hover-bg hover:text-admin-sidebar-fg-strong",
                      )}
                    >
                      {/* active 좌측 accent bar — teal-300 — visual anchor */}
                      {active && (
                        <span
                          aria-hidden
                          className="absolute -left-3 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r-full bg-accent-default"
                        />
                      )}
                      <span aria-hidden className="text-sm leading-none">
                        {item.icon}
                      </span>
                      <span className="truncate">{item.label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
    </aside>
  );
}
