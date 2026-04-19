/**
 * Browser-side API client.
 *
 * All requests go through ``/api/proxy/...`` (BFF). Cookies travel
 * automatically; the proxy translates them into the upstream Bearer token.
 *
 * On a 401 we attempt one ``/api/auth/refresh`` and replay. If the refresh
 * also 401s the user is sent to /login — no cookie left to recover from.
 */
import type { paths } from "./types";

export type ApiError = {
  status: number;
  detail: string;
  payload?: unknown;
};

const PROXY_PREFIX = "/api/proxy";

let refreshInFlight: Promise<boolean> | null = null;

async function refreshOnce(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = fetch("/api/auth/refresh", { method: "POST" })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

async function rawRequest(
  path: string,
  init: RequestInit & { query?: Record<string, string | number | undefined> } = {},
): Promise<Response> {
  const { query, body, headers: callerHeaders, ...rest } = init;
  let url = `${PROXY_PREFIX}/${path.replace(/^\//, "")}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null) params.set(k, String(v));
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  // Only set Content-Type for plain JSON bodies. FormData / Blob need the
  // browser to compute the multipart boundary or carry their own type.
  const isPlainBody =
    typeof body === "string" || (body instanceof ArrayBuffer);
  const finalHeaders: HeadersInit = {
    ...(isPlainBody ? { "Content-Type": "application/json" } : {}),
    ...(callerHeaders ?? {}),
  };
  return fetch(url, {
    ...rest,
    body,
    headers: finalHeaders,
  });
}

export async function request<T>(
  path: string,
  init: Parameters<typeof rawRequest>[1] = {},
): Promise<T> {
  let res = await rawRequest(path, init);
  if (res.status === 401) {
    const refreshed = await refreshOnce();
    if (refreshed) {
      res = await rawRequest(path, init);
    }
  }
  if (!res.ok) {
    const payload = await safeJson(res);
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : res.statusText;
    if (res.status === 401 && typeof window !== "undefined") {
      // Refresh failed too — bounce to login.
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    }
    const err: ApiError = { status: res.status, detail, payload };
    throw err;
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function safeJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

// Re-export for convenience — endpoints.ts pulls request types from here.
export type { paths };
