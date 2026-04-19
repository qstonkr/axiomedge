import { NextResponse } from "next/server";

import {
  ACCESS_COOKIE,
  API_URL,
  REFRESH_COOKIE,
  cookieAttrs,
} from "@/lib/api/url";

const ACCESS_TTL = 60 * 60; // 60 min — must mirror FastAPI access_token_expire_minutes
const REFRESH_TTL = 60 * 60 * 8; // 8 h — must mirror refresh_token_expire_hours

/**
 * BFF login — proxies to FastAPI then materialises HttpOnly cookies.
 *
 * The FastAPI endpoint already sets cookies on its Response, but those are
 * scoped to FastAPI's host. Because the browser only ever talks to Next.js,
 * we have to set them ourselves on this Response. We extract the tokens by
 * reading FastAPI's Set-Cookie headers (it puts ``access_token`` and
 * ``refresh_token`` there in addition to the JSON body).
 */
export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { detail: "Invalid JSON body" },
      { status: 400 },
    );
  }

  const upstream = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const json = await safeJson(upstream);

  if (!upstream.ok) {
    return NextResponse.json(json ?? { detail: upstream.statusText }, {
      status: upstream.status,
    });
  }

  const { access, refresh } = extractTokens(upstream);
  const res = NextResponse.json({
    success: true,
    user: (json as { user?: unknown })?.user,
    roles: (json as { roles?: unknown })?.roles ?? [],
    active_org_id: (json as { active_org_id?: string | null })?.active_org_id ?? null,
  });

  const secure = process.env.NODE_ENV === "production";
  if (access) res.cookies.set(ACCESS_COOKIE, access, cookieAttrs(ACCESS_TTL, secure));
  if (refresh) res.cookies.set(REFRESH_COOKIE, refresh, cookieAttrs(REFRESH_TTL, secure));
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
  // Headers#getSetCookie is available on Node 20+; fall back to the raw
  // header string if not present.
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
