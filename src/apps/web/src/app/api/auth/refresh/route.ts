import { NextResponse } from "next/server";
import { cookies } from "next/headers";

import {
  ACCESS_COOKIE,
  API_URL,
  REFRESH_COOKIE,
  cookieAttrs,
} from "@/lib/api/url";

const ACCESS_TTL = 60 * 60;
const REFRESH_TTL = 60 * 60 * 8;

/**
 * BFF refresh — exchanges the refresh cookie for a new access/refresh pair.
 * Used by the proxy route handler when an upstream call returns 401.
 */
export async function POST() {
  const jar = await cookies();
  const refresh = jar.get(REFRESH_COOKIE)?.value;
  if (!refresh) {
    return NextResponse.json(
      { detail: "Missing refresh token" },
      { status: 401 },
    );
  }

  const upstream = await fetch(`${API_URL}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Cookie: `${REFRESH_COOKIE}=${refresh}` },
    body: "{}",
  });

  if (!upstream.ok) {
    const detail = await safeJson(upstream);
    const res = NextResponse.json(detail ?? { detail: "Refresh rejected" }, {
      status: upstream.status,
    });
    // Clear cookies so the client falls into the login flow next time.
    res.cookies.delete(ACCESS_COOKIE);
    res.cookies.delete(REFRESH_COOKIE);
    return res;
  }

  const { access, refresh: newRefresh } = extractTokens(upstream);
  const res = NextResponse.json({ success: true });
  const secure = process.env.NODE_ENV === "production";
  if (access) res.cookies.set(ACCESS_COOKIE, access, cookieAttrs(ACCESS_TTL, secure));
  if (newRefresh) res.cookies.set(REFRESH_COOKIE, newRefresh, cookieAttrs(REFRESH_TTL, secure));
  return res;
}

async function safeJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

function extractTokens(res: Response): {
  access: string | null;
  refresh: string | null;
} {
  const setCookies =
    typeof (res.headers as Headers & {
      getSetCookie?: () => string[];
    }).getSetCookie === "function"
      ? (res.headers as Headers & { getSetCookie: () => string[] }).getSetCookie()
      : (res.headers.get("set-cookie")?.split(/,(?=[^ ])/) ?? []);

  let access: string | null = null;
  let refresh: string | null = null;
  for (const raw of setCookies) {
    const name = raw.split("=")[0]?.trim();
    const value = raw.split("=")[1]?.split(";")[0];
    if (!name || !value) continue;
    if (name === ACCESS_COOKIE) access = decodeURIComponent(value);
    else if (name === REFRESH_COOKIE) refresh = decodeURIComponent(value);
  }
  return { access, refresh };
}
