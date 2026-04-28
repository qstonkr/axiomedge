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
      <div className="flex min-h-screen">
        <Sidebar userEmail={session.email} />
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 items-center justify-between border-b border-border-default bg-bg-subtle px-8">
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
            className="flex min-w-0 flex-1 flex-col bg-bg-canvas focus:outline-none"
          >
            {children}
          </main>
        </div>
      </div>
    </AppProviders>
  );
}
