import { NextResponse } from "next/server";
import { cookies } from "next/headers";

import { ACCESS_COOKIE, API_URL, REFRESH_COOKIE } from "@/lib/api/url";

export async function POST() {
  const jar = await cookies();
  const access = jar.get(ACCESS_COOKIE)?.value;

  // Best-effort backend revoke. Failure here doesn't block clearing cookies
  // — we still want the client to be logged out locally.
  if (access) {
    await fetch(`${API_URL}/api/v1/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${access}` },
    }).catch(() => undefined);
  }

  const res = NextResponse.json({ success: true });
  res.cookies.delete(ACCESS_COOKIE);
  res.cookies.delete(REFRESH_COOKIE);
  return res;
}
