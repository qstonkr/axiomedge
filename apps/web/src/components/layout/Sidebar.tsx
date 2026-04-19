"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/components/ui/cn";

/**
 * User-facing sidebar — only the 6 MVP pages (Streamlit Group 1+2 + 내 지식).
 * Admin entries land in B-2. Active route is highlighted via usePathname.
 */
const NAV: { href: string; label: string; icon: string }[] = [
  { href: "/chat", label: "지식 검색", icon: "💬" },
  { href: "/find-owner", label: "담당자 찾기", icon: "👤" },
  { href: "/my-knowledge", label: "내 지식", icon: "📚" },
  { href: "/my-documents", label: "내 담당 문서", icon: "📄" },
  { href: "/my-feedback", label: "피드백/오류 신고", icon: "📝" },
  { href: "/search-history", label: "검색 이력", icon: "🕐" },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="hidden w-64 shrink-0 border-r border-border-default bg-bg-subtle px-3 py-4 md:block">
      <nav className="space-y-1" aria-label="사용자 메뉴">
        {NAV.map((item) => {
          const active =
            pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-bg-emphasis font-medium text-fg-default"
                  : "text-fg-muted hover:bg-bg-muted hover:text-fg-default",
              )}
            >
              <span aria-hidden className="text-base leading-none">
                {item.icon}
              </span>
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
