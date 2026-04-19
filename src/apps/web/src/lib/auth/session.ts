import "server-only";

import { cookies } from "next/headers";

import { ACCESS_COOKIE, API_URL, REFRESH_COOKIE } from "@/lib/api/url";

export type Membership = {
  organization_id: string;
  role: string;
  status: string;
  joined_at: string | null;
};

export type Session = {
  sub: string;
  email: string;
  display_name: string;
  provider: string;
  active_org_id: string | null;
  organization_id: string | null;
  memberships: Membership[];
  roles: { role: string; scope_type?: string | null; scope_id?: string | null }[];
  permissions: string[];
};

/**
 * Server-only — reads the access cookie and asks FastAPI ``/auth/me``.
 *
 * Returns ``null`` when there is no cookie or the backend rejects the token
 * (expired, revoked, missing org membership). The middleware/layout above
 * decides what to do with that — usually redirect to ``/login``.
 *
 * NOTE: this does NOT trigger refresh on its own. The refresh dance lives
 * in the proxy route handler so server components remain side-effect free.
 */
export async function getSession(): Promise<Session | null> {
  const jar = await cookies();
  const access = jar.get(ACCESS_COOKIE)?.value;
  if (!access) return null;

  try {
    const res = await fetch(`${API_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${access}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as Session;
  } catch {
    return null;
  }
}

/** Convenience — true when the cookie jar holds at least an access token. */
export async function hasSessionCookie(): Promise<boolean> {
  const jar = await cookies();
  return jar.has(ACCESS_COOKIE) || jar.has(REFRESH_COOKIE);
}
