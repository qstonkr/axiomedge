import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppProviders } from "@/components/providers";
import { Sidebar } from "@/components/layout/Sidebar";
import { OrgSwitcher } from "@/components/layout/OrgSwitcher";
import { PrivacyConsent } from "@/components/PrivacyConsent";
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
      <PrivacyConsent />
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-accent-default focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-fg-onAccent"
      >
        본문으로 건너뛰기
      </a>
      {/* h-screen + min-h-0 children so the chat 3-pane (which uses h-full
          inside flex-1) can actually compute height. Without min-h-0 the
          flex parent expands to fit content and h-full collapses to 0. */}
      <div className="flex h-screen min-h-screen">
        <Sidebar userEmail={session.email} />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center justify-between border-b border-border-default bg-bg-subtle px-8">
            <span className="text-base font-semibold tracking-tight text-fg-default">
              axiomedge
            </span>
            <OrgSwitcher
              activeOrgId={session.active_org_id}
              memberships={session.memberships}
              displayName={session.display_name}
            />
          </header>
          <main
            id="main-content"
            tabIndex={-1}
            className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto bg-bg-canvas focus:outline-none"
          >
            {children}
          </main>
        </div>
      </div>
    </AppProviders>
  );
}
