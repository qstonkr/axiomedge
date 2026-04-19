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

export async function POST(req: Request) {
  const jar = await cookies();
  const access = jar.get(ACCESS_COOKIE)?.value;
  if (!access) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body" }, { status: 400 });
  }

  const upstream = await fetch(`${API_URL}/api/v1/auth/switch-org`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${access}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  const json = await safeJson(upstream);
  if (!upstream.ok) {
    return NextResponse.json(json ?? { detail: upstream.statusText }, {
      status: upstream.status,
    });
  }

  const { access: newAccess, refresh: newRefresh } = extractTokens(upstream);
  const res = NextResponse.json(json ?? { success: true });
  const secure = process.env.NODE_ENV === "production";
  if (newAccess) res.cookies.set(ACCESS_COOKIE, newAccess, cookieAttrs(ACCESS_TTL, secure));
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
