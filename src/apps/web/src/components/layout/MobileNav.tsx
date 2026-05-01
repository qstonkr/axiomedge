"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { BookOpen, FileText, Menu, MessageCircle, X, type LucideIcon } from "lucide-react";

import { cn } from "@/components/ui/cn";

/**
 * Mobile slide-out navigation — md 미만 화면에서만 표시 (B3).
 *
 * 데스크톱 outer Sidebar 가 ``hidden md:flex`` 로 모바일에서는 안 보이는데
 * 그 대체 진입점이 부재했음. (app) layout header 에 hamburger 를 두고 클릭 시
 * 좌측 슬라이드 패널이 열리며 동일한 nav entries 노출.
 *
 * 의존성 회피: Dialog/Drawer 별도 라이브러리 없이 fixed overlay + transition.
 */
const NAV: { href: string; key: string; Icon: LucideIcon }[] = [
  { href: "/chat", key: "chat", Icon: MessageCircle },
  { href: "/my-knowledge", key: "my_knowledge", Icon: BookOpen },
  { href: "/my-documents", key: "my_documents", Icon: FileText },
];

export function MobileNav() {
  const pathname = usePathname();
  const t = useTranslations("nav");
  const [open, setOpen] = useState(false);

  // route 이동 시 자동 닫힘 — pathname 이 외부 input (router) 이고 menu state 를
  // 그에 동기화하는 정당한 effect 용도. PrivacyConsent.tsx 와 동일 패턴.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOpen(false);
  }, [pathname]);

  // Esc 닫기 + 열림 시 body scroll lock
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t("label")}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="inline-flex h-9 w-9 items-center justify-center rounded-md text-fg-default transition-colors hover:bg-bg-muted md:hidden"
      >
        <Menu size={20} strokeWidth={1.75} aria-hidden />
      </button>

      {open && (
        <>
          {/* Backdrop — click outside to close */}
          <div
            aria-hidden
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-40 bg-fg-default/40 backdrop-blur-sm md:hidden"
          />
          <aside
            role="dialog"
            aria-label={t("label")}
            aria-modal="true"
            className="fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-border-default bg-bg-subtle px-3 py-4 shadow-lg md:hidden"
          >
            <div className="mb-4 flex items-center justify-between px-1">
              <span className="text-base font-semibold tracking-tight text-fg-default">
                axiomedge
              </span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="닫기"
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-muted"
              >
                <X size={18} strokeWidth={1.75} aria-hidden />
              </button>
            </div>
            <nav className="flex-1 space-y-1" aria-label={t("label")}>
              {NAV.map(({ href, key, Icon }) => {
                const active = pathname === href || pathname.startsWith(`${href}/`);
                return (
                  <Link
                    key={href}
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                      active
                        ? "bg-bg-emphasis font-medium text-fg-default"
                        : "text-fg-muted hover:bg-bg-muted hover:text-fg-default",
                    )}
                  >
                    <Icon aria-hidden size={18} strokeWidth={1.75} />
                    <span>{t(key)}</span>
                  </Link>
                );
              })}
            </nav>
          </aside>
        </>
      )}
    </>
  );
}
