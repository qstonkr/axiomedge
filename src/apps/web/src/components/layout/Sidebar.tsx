"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { BookOpen, FileText, MessageCircle, type LucideIcon } from "lucide-react";

import { cn } from "@/components/ui/cn";

import { ProfileDropdown } from "./ProfileDropdown";

/**
 * User-facing outer sidebar — always present (B1).
 *
 * /chat 안에서는 narrow icon rail (56px) 로 collapse 해서 ConversationSidebar
 * 옆에 nest. 다른 페이지에서는 expanded (224px) 로 라벨 노출.
 *
 * 이전 (Sidebar.tsx PR4): /chat 진입 시 `return null` 로 outer nav 자체 사라짐 →
 * chat ↔ my-knowledge 이동 시 좌측 nav 통째로 swap → 위치 인지 비용. VS Code /
 * Linear / Cursor 패턴으로 통일 (rail-always-on).
 */
const NAV: { href: string; key: string; Icon: LucideIcon }[] = [
  { href: "/chat", key: "chat", Icon: MessageCircle },
  { href: "/my-knowledge", key: "my_knowledge", Icon: BookOpen },
  { href: "/my-documents", key: "my_documents", Icon: FileText },
];

export function Sidebar({ userEmail }: { userEmail?: string } = {}) {
  const pathname = usePathname();
  const t = useTranslations("nav");
  // Chat 안에서는 ConversationSidebar 가 옆에 붙으므로 outer 는 narrow rail.
  const collapsed = pathname.startsWith("/chat");
  return (
    <aside
      className={cn(
        "hidden shrink-0 self-stretch border-r border-border-default bg-bg-subtle py-4 md:flex md:flex-col",
        collapsed ? "w-14 px-2" : "w-56 px-3",
      )}
    >
      <nav className="flex-1 space-y-1" aria-label={t("label")}>
        {NAV.map(({ href, key, Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              title={collapsed ? t(key) : undefined}
              className={cn(
                "flex items-center rounded-md text-sm transition-colors",
                collapsed ? "justify-center px-0 py-2" : "gap-3 px-3 py-2",
                active
                  ? "bg-bg-emphasis font-medium text-fg-default"
                  : "text-fg-muted hover:bg-bg-muted hover:text-fg-default",
              )}
            >
              <Icon aria-hidden size={18} strokeWidth={1.75} />
              {!collapsed && <span>{t(key)}</span>}
            </Link>
          );
        })}
      </nav>
      {userEmail && !collapsed && (
        <div className="mt-2 border-t border-border-default pt-2">
          <ProfileDropdown email={userEmail} />
        </div>
      )}
    </aside>
  );
}
