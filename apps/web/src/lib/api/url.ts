/**
 * Where the BFF (server-side route handlers) reach the FastAPI backend.
 * Set ``API_URL`` (env, server-only) to override; localhost default lets
 * `make web-dev` pair with `make api` out of the box.
 */
export const API_URL = process.env.API_URL ?? "http://localhost:8000";

export const ACCESS_COOKIE = "access_token";
export const REFRESH_COOKIE = "refresh_token";

/** Cookie attributes shared by every auth route. */
export function cookieAttrs(maxAgeSeconds: number, secure: boolean) {
  return {
    httpOnly: true,
    secure,
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}
