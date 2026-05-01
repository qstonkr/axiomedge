"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Activity, Lock, LogOut, MessageSquareWarning, ShieldOff, User } from "lucide-react";

export function ProfileDropdown({ email }: { email: string }) {
  const [open, setOpen] = useState(false);
  const [withdrawing, setWithdrawing] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Close on outside click + Escape — was open until next page load before.
  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function withdrawConsent() {
    if (
      !confirm(
        "처리방침 동의를 철회하시겠어요?\n\n" +
          "철회 후에는 채팅 사용 시 동의 안내 화면이 다시 나타납니다.\n" +
          "기존 대화는 자동 삭제되지 않으며, 사이드바에서 직접 삭제할 수 있습니다.",
      )
    ) {
      return;
    }
    setWithdrawing(true);
    try {
      const res = await fetch("/api/proxy/api/v1/users/me/consent", {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 404) {
        // 404 means there's nothing to withdraw — same end state.
        throw new Error("withdraw failed");
      }
    } catch {
      alert("동의 철회 중 오류가 발생했습니다. 다시 시도해 주세요.");
      setWithdrawing(false);
      return;
    }
    // Force the modal to re-prompt: drop the localStorage flag and reload so
    // the next render sees the withdrawn server state.
    localStorage.removeItem("axe-privacy-consent-v1");
    window.location.reload();
  }

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        aria-label="프로필"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm hover:bg-bg-muted focus-visible:outline-2 focus-visible:outline-accent-default"
      >
        <User aria-hidden size={16} strokeWidth={1.75} className="shrink-0" />
        <span className="truncate">{email}</span>
      </button>
      {open && (
        <ul
          role="menu"
          className="absolute bottom-full left-0 z-10 mb-1 w-full overflow-hidden rounded-md border border-border-default bg-bg-canvas shadow-md"
        >
          <li>
            <Link
              role="menuitem"
              href="/my-feedback"
              onClick={() => setOpen(false)}
              className="flex min-h-[36px] items-center gap-2 px-3 py-2 text-sm hover:bg-bg-muted focus-visible:bg-bg-muted focus-visible:outline-none"
            >
              <MessageSquareWarning aria-hidden size={14} strokeWidth={1.75} />
              <span>내 피드백</span>
            </Link>
          </li>
          <li>
            <Link
              role="menuitem"
              href="/my-activities"
              onClick={() => setOpen(false)}
              className="flex min-h-[36px] items-center gap-2 px-3 py-2 text-sm hover:bg-bg-muted focus-visible:bg-bg-muted focus-visible:outline-none"
            >
              <Activity aria-hidden size={14} strokeWidth={1.75} />
              <span>내 활동</span>
            </Link>
          </li>
          <li>
            <Link
              role="menuitem"
              href="/security#chat-retention"
              onClick={() => setOpen(false)}
              className="flex min-h-[36px] items-center gap-2 px-3 py-2 text-sm hover:bg-bg-muted focus-visible:bg-bg-muted focus-visible:outline-none"
            >
              <Lock aria-hidden size={14} strokeWidth={1.75} />
              <span>처리방침</span>
            </Link>
          </li>
          <li>
            <button
              type="button"
              role="menuitem"
              disabled={withdrawing}
              onClick={withdrawConsent}
              className="flex w-full min-h-[36px] items-center gap-2 px-3 py-2 text-left text-sm hover:bg-bg-muted focus-visible:bg-bg-muted focus-visible:outline-none disabled:opacity-60"
            >
              <ShieldOff aria-hidden size={14} strokeWidth={1.75} />
              <span>{withdrawing ? "철회 중…" : "처리방침 동의 철회"}</span>
            </button>
          </li>
          <li>
            <form method="post" action="/api/auth/logout">
              <button
                type="submit"
                role="menuitem"
                className="flex w-full min-h-[36px] items-center gap-2 px-3 py-2 text-left text-sm hover:bg-bg-muted focus-visible:bg-bg-muted focus-visible:outline-none"
              >
                <LogOut aria-hidden size={14} strokeWidth={1.75} />
                <span>로그아웃</span>
              </button>
            </form>
          </li>
        </ul>
      )}
    </div>
  );
}
