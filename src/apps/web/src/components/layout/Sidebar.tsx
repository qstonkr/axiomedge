"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { cn } from "@/components/ui/cn";

import { ProfileDropdown } from "./ProfileDropdown";

/**
 * User-facing outer sidebar — trimmed to non-chat hubs only.
 *
 * /chat owns its own ConversationSidebar (history + new chat) which
 * also renders its own ProfileDropdown. This outer one is hidden inside
 * /chat to avoid double sidebars.
 *
 * Removed entries (PR4 of UX redesign):
 * - /search-history → absorbed into /chat ConversationSidebar
 * - /find-owner → /owner slash command in /chat
 * - /my-feedback, /my-activities → moved into ProfileDropdown (here)
 */
const NAV: { href: string; key: string; icon: string }[] = [
  { href: "/chat", key: "chat", icon: "💬" },
  { href: "/my-knowledge", key: "my_knowledge", icon: "📚" },
  { href: "/my-documents", key: "my_documents", icon: "📄" },
];

export function Sidebar({ userEmail }: { userEmail?: string } = {}) {
  const pathname = usePathname();
  const t = useTranslations("nav");
  if (pathname.startsWith("/chat")) return null;
  return (
    <aside className="hidden w-64 shrink-0 self-stretch border-r border-border-default bg-bg-subtle px-3 py-4 md:flex md:flex-col">
      <nav className="flex-1 space-y-1" aria-label={t("label")}>
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
      {userEmail && (
        <div className="mt-2 border-t border-border-default pt-2">
          <ProfileDropdown email={userEmail} />
        </div>
      )}
    </aside>
  );
}
