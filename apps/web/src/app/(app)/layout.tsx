import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppProviders } from "@/components/providers";
import { Sidebar } from "@/components/layout/Sidebar";
import { OrgSwitcher } from "@/components/layout/OrgSwitcher";
import { getSession } from "@/lib/auth/session";

/**
 * AppShell layout — wraps every authenticated page (chat, find-owner,
 * my-feedback, my-documents, search-history, my-knowledge).
 *
 * AuthGuard:
 *  - No session → redirect to /login (Next.js 16: cookies() is async, so
 *    getSession() must be awaited from a server component)
 *  - Session present → render Sidebar + OrgSwitcher chrome around the page
 */
export default async function AppLayout({
  children,
}: {
  children: ReactNode;
}) {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }

  return (
    <AppProviders>
      <div className="flex min-h-full">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-12 items-center justify-between border-b border-border-default bg-bg-subtle px-6">
            <span className="text-sm font-medium text-fg-default">axiomedge</span>
            <OrgSwitcher
              activeOrgId={session.active_org_id}
              memberships={session.memberships}
              displayName={session.display_name}
            />
          </header>
          <main className="flex min-w-0 flex-1 flex-col bg-bg-canvas">{children}</main>
        </div>
      </div>
    </AppProviders>
  );
}
