import { NextResponse } from "next/server";
import { cookies } from "next/headers";

import { ACCESS_COOKIE, API_URL } from "@/lib/api/url";

/**
 * Generic FastAPI proxy.
 *
 * Browser → ``/api/proxy/api/v1/<anything>`` → FastAPI ``/api/v1/<anything>``.
 * The HttpOnly access cookie never crosses to the browser; it is read here,
 * server-side, and reformulated as ``Authorization: Bearer ...`` for the
 * upstream call. Method, query string, headers (minus host/cookie), and
 * raw body are forwarded unchanged.
 *
 * 401 responses from upstream are surfaced as-is so the client can decide
 * to call ``/api/auth/refresh`` and retry. The retry orchestration lives
 * in the API client (Day 4) — kept out of this route to keep it dumb.
 */
async function handle(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const target = path.join("/");

  const upstreamUrl = new URL(`${API_URL}/${target}`);
  // Forward query string verbatim.
  const incoming = new URL(req.url);
  for (const [k, v] of incoming.searchParams.entries()) {
    upstreamUrl.searchParams.append(k, v);
  }

  const jar = await cookies();
  const access = jar.get(ACCESS_COOKIE)?.value;

  const headers = new Headers();
  // Filter out hop-by-hop and host-tied headers.
  const skip = new Set([
    "host", "connection", "content-length", "transfer-encoding",
    "cookie", "authorization",
  ]);
  for (const [k, v] of req.headers.entries()) {
    if (!skip.has(k.toLowerCase())) headers.set(k, v);
  }
  if (access) headers.set("Authorization", `Bearer ${access}`);

  const init: RequestInit = {
    method: req.method,
    headers,
    cache: "no-store",
    redirect: "manual",
  };
  // GET/HEAD must not include a body.
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }

  const upstream = await fetch(upstreamUrl, init);

  // Pass through Set-Cookie from upstream (rare — most are auth-only and
  // those have dedicated routes).
  const respHeaders = new Headers();
  for (const [k, v] of upstream.headers.entries()) {
    if (k.toLowerCase() === "set-cookie") continue; // skip, NextResponse handles cookies
    respHeaders.set(k, v);
  }

  return new NextResponse(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: respHeaders,
  });
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
