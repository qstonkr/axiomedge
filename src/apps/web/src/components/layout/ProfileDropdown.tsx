"use client";

import Link from "next/link";
import { useState } from "react";

export function ProfileDropdown({ email }: { email: string }) {
  const [open, setOpen] = useState(false);
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
