"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { cn } from "@/components/ui/cn";

/**
 * User-facing sidebar — only the 6 MVP pages (Streamlit Group 1+2 + 내 지식).
 * Admin entries land in B-2. Active route is highlighted via usePathname.
 */
const NAV: { href: string; key: string; icon: string }[] = [
  { href: "/chat", key: "chat", icon: "💬" },
  { href: "/find-owner", key: "find_owner", icon: "👤" },
  { href: "/my-knowledge", key: "my_knowledge", icon: "📚" },
  { href: "/my-documents", key: "my_documents", icon: "📄" },
  { href: "/my-feedback", key: "my_feedback", icon: "📝" },
  { href: "/search-history", key: "search_history", icon: "🕐" },
];

export function Sidebar() {
  const pathname = usePathname();
  const t = useTranslations("nav");
  return (
    <aside className="hidden w-64 shrink-0 border-r border-border-default bg-bg-subtle px-3 py-4 md:block">
      <nav className="space-y-1" aria-label={t("label")}>
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
              <span>{t(item.key)}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
