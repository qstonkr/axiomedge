"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/components/ui/cn";

import { groupAdminNav } from "./nav";

/**
 * Admin sidebar — 5 그룹 메뉴.
 * 운영자가 한눈에 모든 admin 영역을 볼 수 있도록 항상 펼친 상태.
 * dark bg 는 light/dark theme 와 무관하게 유지 (Sentry/Posthog 패턴).
 *
 * NAV 정의는 ./nav.ts SSOT — QuickPalette / MobileNav 와 공유.
 */
export function AdminSidebar() {
  const pathname = usePathname();
  const groups = groupAdminNav();

  return (
    <aside
      className="hidden w-60 shrink-0 self-stretch overflow-y-auto bg-admin-sidebar-bg px-3 py-4 text-admin-sidebar-fg lg:block"
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
        {groups.map((group, idx) => (
          <div key={group.label}>
            {idx > 0 && (
              <div
                aria-hidden
                className="mb-3 h-px"
                style={{ background: "var(--color-admin-sidebar-border)" }}
              />
            )}
            <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-wider text-admin-sidebar-fg/60">
              {group.label}
            </p>
            <ul className="space-y-0.5">
              {group.items.map(({ href, label, Icon }) => {
                const active =
                  pathname === href ||
                  (href !== "/admin" && pathname.startsWith(`${href}/`));
                return (
                  <li key={href}>
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "relative flex items-center gap-2.5 rounded-md px-3 py-1.5 text-xs transition-colors",
                        active
                          ? "bg-admin-sidebar-active-bg text-admin-sidebar-active-fg font-medium"
                          : "text-admin-sidebar-fg hover:bg-admin-sidebar-hover-bg hover:text-admin-sidebar-fg-strong",
                      )}
                    >
                      {/* active 좌측 accent bar — teal — visual anchor */}
                      {active && (
                        <span
                          aria-hidden
                          className="absolute -left-3 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r-full bg-accent-default"
                        />
                      )}
                      <Icon aria-hidden size={14} strokeWidth={1.75} className="shrink-0" />
                      <span className="truncate">{label}</span>
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
