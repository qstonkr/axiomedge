import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AdminHeader } from "@/components/admin/AdminHeader";
import { AdminSidebar } from "@/components/admin/AdminSidebar";
import { hasAdminRole } from "@/components/admin/adminGuard";
import { AppProviders } from "@/components/providers";
import { getSession } from "@/lib/auth/session";

/**
 * Admin route group layout. Two gates:
 *   1. No session → /login (proxy.ts already covers this, defense in depth)
 *   2. Session 이지만 OWNER/ADMIN role 없음 → /chat (사용자 화면으로 돌려보냄)
 *
 * Visual differentiation 은 AdminHeader 가 mount 시
 * `<html data-admin="true">` 를 set 하여 globals.css 의 [data-admin] 토큰
 * (teal accent + dark sidebar) 활성화로 처리.
 */
export default async function AdminLayout({
  children,
}: {
  children: ReactNode;
}) {
  const session = await getSession();
  if (!session) {
    redirect("/login?next=%2Fadmin");
  }
  if (!hasAdminRole(session)) {
    // role 없으면 사용자 화면으로 — admin 권한 없는 사용자가 접근하면 조용히 차단
    redirect("/chat");
  }

  return (
    <AppProviders>
      <a
        href="#admin-main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-accent-default focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-fg-onAccent"
      >
        본문으로 건너뛰기
      </a>
      <div className="flex min-h-screen">
        <AdminSidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <AdminHeader
            activeOrgId={session.active_org_id}
            memberships={session.memberships}
            displayName={session.display_name}
          />
          <main
            id="admin-main"
            tabIndex={-1}
            className="flex min-w-0 flex-1 flex-col overflow-auto bg-bg-canvas p-6 focus:outline-none"
          >
            {children}
          </main>
        </div>
      </div>
    </AppProviders>
  );
}
