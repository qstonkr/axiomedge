import { NextResponse, type NextRequest } from "next/server";

import { ACCESS_COOKIE, REFRESH_COOKIE } from "@/lib/api/url";

/**
 * Next.js 16 renamed ``middleware.ts`` → ``proxy.ts``. Same semantics:
 * runs before every request that matches ``config.matcher``.
 *
 * Job: send anonymous browser traffic to ``/login`` before the (app) layout
 * even renders. AppLayout still calls getSession() as defense-in-depth, but
 * the redirect here saves a server round-trip + flash of unstyled gate.
 */
/**
 * 30-day redirect map for pages absorbed into /chat (PR4 of UX redesign).
 * Drop entries after ~2026-05-28 once user habit + bookmarks have moved on.
 */
const REDIRECTS_ABSORBED: Record<string, string> = {
  "/search-history": "/chat",
  "/find-owner": "/chat?onboarding=owner",
};

export function proxy(req: NextRequest) {
  const { pathname, search } = req.nextUrl;

  // Permanent-ish redirect (308) for pages absorbed into /chat.
  const redirectTarget = REDIRECTS_ABSORBED[pathname];
  if (redirectTarget) {
    const url = req.nextUrl.clone();
    const [redirPath, redirQuery] = redirectTarget.split("?");
    url.pathname = redirPath;
    url.search = redirQuery ? `?${redirQuery}` : "";
    return NextResponse.redirect(url, 308);
  }

  if (isPublic(pathname)) return NextResponse.next();

  const hasAccess = req.cookies.has(ACCESS_COOKIE);
  const hasRefresh = req.cookies.has(REFRESH_COOKIE);
  if (hasAccess || hasRefresh) return NextResponse.next();

  const loginUrl = req.nextUrl.clone();
  loginUrl.pathname = "/login";
  loginUrl.search = `?next=${encodeURIComponent(pathname + search)}`;
  return NextResponse.redirect(loginUrl);
}

function isPublic(pathname: string): boolean {
  if (pathname === "/login") return true;
  if (pathname.startsWith("/api/auth/")) return true;
  if (pathname.startsWith("/_next/")) return true;
  if (pathname.startsWith("/favicon")) return true;
  if (pathname.startsWith("/static/")) return true;
  return false;
}

/**
 * Skip the matcher for asset/internal paths so the proxy stays cheap.
 * /api/proxy/* still hits this — but those calls already carry the cookie
 * if the user is logged in, so no redirect.
 */
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
