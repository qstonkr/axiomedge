"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import type { Membership } from "@/lib/auth/session";

type Props = {
  activeOrgId: string | null;
  memberships: Membership[];
  displayName: string;
};

export function OrgSwitcher({ activeOrgId, memberships, displayName }: Props) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  function pickOrg(orgId: string) {
    if (orgId === activeOrgId) return;
    setError(null);
    startTransition(async () => {
      const res = await fetch("/api/auth/switch-org", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ organization_id: orgId }),
      });
      if (!res.ok) {
        const json = await res.json().catch(() => null);
        setError(json?.detail ?? `전환 실패 (${res.status})`);
        return;
      }
      router.refresh();
    });
  }

  // Single-membership users get a static label; multi-membership users
  // see a select. Either way, "로그아웃" is one click away.
  return (
    <div className="flex items-center gap-3 text-xs text-fg-muted">
      {memberships.length > 1 ? (
        <label className="flex items-center gap-2">
          <span className="hidden sm:inline">조직</span>
          <select
            value={activeOrgId ?? ""}
            onChange={(e) => pickOrg(e.target.value)}
            disabled={pending}
            className="h-7 rounded-md border border-border-default bg-bg-canvas px-2 text-xs text-fg-default focus:border-accent-default focus:outline-none disabled:opacity-50"
          >
            {memberships.map((m) => (
              <option key={m.organization_id} value={m.organization_id}>
                {m.organization_id}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <span className="hidden sm:inline">{activeOrgId ?? "—"}</span>
      )}
      <span aria-hidden>·</span>
      <span className="text-fg-default">{displayName}</span>
      <button
        type="button"
        onClick={logout}
        className="rounded-md px-2 py-1 text-xs text-fg-muted transition-colors hover:bg-bg-muted hover:text-fg-default"
      >
        로그아웃
      </button>
      {error && (
        <span role="alert" className="text-danger-default">
          {error}
        </span>
      )}
    </div>
  );
}
