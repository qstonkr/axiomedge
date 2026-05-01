"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Menu, X } from "lucide-react";

import { cn } from "@/components/ui/cn";

import { groupAdminNav } from "./nav";

/**
 * Admin mobile/tablet slide-out — lg 미만 화면 진입점 (B-2 follow-up).
 *
 * 데스크탑 AdminSidebar 가 ``hidden lg:block`` 라 모바일/태블릿에서는 사이드바
 * 가 보이지 않아 페이지 간 이동 불가. AdminHeader 에 햄버거 trigger 를 두고
 * 클릭 시 좌측 슬라이드 패널이 열리며 5 그룹 nav 노출. md(768) 에서 보였던
 * 사이드바가 컨텐츠를 압박해 한글 어절이 글자 단위로 깨졌던 문제 해소용.
 *
 * Sidebar 와 동일한 dark slate 배경 + teal accent 유지. focus trap + Esc + 외부 클릭 닫기.
 */
export function AdminMobileNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const firstLinkRef = useRef<HTMLAnchorElement>(null);

  // route 변경 시 자동 닫기.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOpen(false);
  }, [pathname]);

  // 열림 시 body lock + Tab focus trap + Esc.
  useEffect(() => {
    if (!open) return;
    firstLinkRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        return;
      }
      if (e.key === "Tab" && panelRef.current) {
        const focusables = panelRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        );
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (e.shiftKey && active === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
      triggerRef.current?.focus();
    };
  }, [open]);

  const groups = groupAdminNav();

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-label="관리자 메뉴 열기"
        aria-haspopup="dialog"
        aria-expanded={open}
        className="inline-flex h-9 w-9 items-center justify-center rounded-md text-fg-default transition-colors hover:bg-bg-muted lg:hidden"
      >
        <Menu size={20} strokeWidth={1.75} aria-hidden />
      </button>

      {open && (
        <>
          <div
            aria-hidden
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-40 bg-fg-default/40 backdrop-blur-sm lg:hidden"
          />
          <aside
            ref={panelRef}
            role="dialog"
            aria-label="관리자 메뉴"
            aria-modal="true"
            className="fixed inset-y-0 left-0 z-50 flex w-72 flex-col overflow-y-auto bg-admin-sidebar-bg px-3 py-4 text-admin-sidebar-fg shadow-lg lg:hidden"
            style={{ borderRight: "1px solid var(--color-admin-sidebar-border)" }}
          >
            <div className="mb-4 flex items-center justify-between px-1">
              <Link
                href="/admin"
                className="flex items-center gap-2 text-sm font-semibold text-admin-sidebar-fg-strong"
              >
                axiomedge
                <span className="rounded bg-accent-default px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-fg-onAccent">
                  Admin
                </span>
              </Link>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="닫기"
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-admin-sidebar-fg transition-colors hover:bg-admin-sidebar-hover-bg"
              >
                <X size={18} strokeWidth={1.75} aria-hidden />
              </button>
            </div>

            <nav className="flex-1 space-y-5" aria-label="관리자 메뉴">
              {groups.map((group, idx) => {
                let isFirstAcrossGroups = idx === 0;
                return (
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
                      {group.items.map(({ href, label, Icon }, itemIdx) => {
                        const active =
                          pathname === href ||
                          (href !== "/admin" && pathname.startsWith(`${href}/`));
                        const isFirst = isFirstAcrossGroups && itemIdx === 0;
                        if (isFirst) isFirstAcrossGroups = false;
                        return (
                          <li key={href}>
                            <Link
                              ref={isFirst ? firstLinkRef : undefined}
                              href={href}
                              aria-current={active ? "page" : undefined}
                              className={cn(
                                "flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                                active
                                  ? "bg-admin-sidebar-active-bg text-admin-sidebar-active-fg font-medium"
                                  : "text-admin-sidebar-fg hover:bg-admin-sidebar-hover-bg hover:text-admin-sidebar-fg-strong",
                              )}
                            >
                              <Icon aria-hidden size={14} strokeWidth={1.75} className="shrink-0" />
                              <span className="truncate">{label}</span>
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                );
              })}
            </nav>
          </aside>
        </>
      )}
    </>
  );
}
