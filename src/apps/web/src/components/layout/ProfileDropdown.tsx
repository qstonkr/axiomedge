"use client";

import Link from "next/link";
import { useState } from "react";

export function ProfileDropdown({ email }: { email: string }) {
  const [open, setOpen] = useState(false);
  const [withdrawing, setWithdrawing] = useState(false);

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
    <div className="relative">
      <button
        aria-label="프로필"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm hover:bg-bg-muted"
      >
        <span aria-hidden>👤</span>
        <span className="truncate">{email}</span>
      </button>
      {open && (
        <ul
          role="menu"
          className="absolute bottom-full left-0 z-10 mb-1 w-full overflow-hidden rounded-md border border-border-default bg-bg-default shadow-lg"
        >
          <li>
            <Link
              role="menuitem"
              href="/my-feedback"
              className="block px-3 py-2 text-sm hover:bg-bg-muted"
            >
              📝 내 피드백
            </Link>
          </li>
          <li>
            <Link
              role="menuitem"
              href="/my-activities"
              className="block px-3 py-2 text-sm hover:bg-bg-muted"
            >
              📋 내 활동
            </Link>
          </li>
          <li>
            <Link
              role="menuitem"
              href="/security#chat-retention"
              className="block px-3 py-2 text-sm hover:bg-bg-muted"
            >
              🔒 처리방침
            </Link>
          </li>
          <li>
            <button
              type="button"
              role="menuitem"
              disabled={withdrawing}
              onClick={withdrawConsent}
              className="block w-full px-3 py-2 text-left text-sm hover:bg-bg-muted disabled:opacity-60"
            >
              🚫 {withdrawing ? "철회 중…" : "처리방침 동의 철회"}
            </button>
          </li>
          <li>
            <form method="post" action="/api/auth/logout">
              <button
                type="submit"
                role="menuitem"
                className="block w-full px-3 py-2 text-left text-sm hover:bg-bg-muted"
              >
                ⏻ 로그아웃
              </button>
            </form>
          </li>
        </ul>
      )}
    </div>
  );
}
