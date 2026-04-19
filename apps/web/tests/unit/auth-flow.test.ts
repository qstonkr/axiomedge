/**
 * BFF auth-route smoke tests (B-1 Day 3).
 *
 * We exercise the route handlers in isolation by stubbing global ``fetch``
 * (the upstream FastAPI call) and the ``next/headers`` cookie store.
 * Goal: every route correctly proxies, sets/clears the right cookies, and
 * forwards upstream errors verbatim.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ACCESS_COOKIE, REFRESH_COOKIE } from "@/lib/api/url";

type CookieJar = Map<string, string>;

function mockCookieJar(initial: Record<string, string> = {}): CookieJar {
  const jar: CookieJar = new Map(Object.entries(initial));
  vi.doMock("next/headers", () => ({
    cookies: async () => ({
      get: (name: string) =>
        jar.has(name) ? { name, value: jar.get(name)! } : undefined,
      has: (name: string) => jar.has(name),
      set: (name: string, value: string) => jar.set(name, value),
      delete: (name: string) => jar.delete(name),
    }),
  }));
  return jar;
}

function fakeUpstream(status: number, body: unknown, setCookies: string[] = []) {
  const headers = new Headers({ "Content-Type": "application/json" });
  for (const c of setCookies) headers.append("set-cookie", c);
  return new Response(JSON.stringify(body), { status, headers });
}

afterEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

describe("/api/auth/login", () => {
  it("forwards body, returns 401 unchanged on bad creds", async () => {
    mockCookieJar();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        fakeUpstream(401, { detail: "Invalid email or password" }),
      ),
    );

    const { POST } = await import("@/app/api/auth/login/route");
    const req = new Request("http://localhost/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: "x@y", password: "wrong" }),
    });
    const res = await POST(req);

    expect(res.status).toBe(401);
    expect(await res.json()).toMatchObject({
      detail: "Invalid email or password",
    });
  });

  it("sets HttpOnly cookies on success", async () => {
    mockCookieJar();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        fakeUpstream(
          200,
          { success: true, user: { id: "u1" }, roles: ["MEMBER"], active_org_id: "org-1" },
          [
            `${ACCESS_COOKIE}=jwt-access; Path=/; HttpOnly`,
            `${REFRESH_COOKIE}=jwt-refresh; Path=/; HttpOnly`,
          ],
        ),
      ),
    );

    const { POST } = await import("@/app/api/auth/login/route");
    const req = new Request("http://localhost/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: "u@test", password: "ok" }),
    });
    const res = await POST(req);

    expect(res.status).toBe(200);
    const setCookieHeader = res.headers.get("set-cookie") ?? "";
    expect(setCookieHeader).toContain(`${ACCESS_COOKIE}=jwt-access`);
    expect(setCookieHeader).toContain(`${REFRESH_COOKIE}=jwt-refresh`);
    expect(setCookieHeader.toLowerCase()).toContain("httponly");
    const json = await res.json();
    expect(json.active_org_id).toBe("org-1");
  });
});

describe("/api/auth/logout", () => {
  it("clears cookies and best-effort calls upstream", async () => {
    mockCookieJar({ [ACCESS_COOKIE]: "jwt", [REFRESH_COOKIE]: "rfsh" });
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await import("@/app/api/auth/logout/route");
    const res = await POST();

    expect(res.status).toBe(200);
    const cookies = res.headers.get("set-cookie") ?? "";
    expect(cookies).toContain(`${ACCESS_COOKIE}=`);
    expect(cookies).toMatch(/Max-Age=0|Expires=Thu, 01 Jan 1970/);
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/auth/logout"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});

describe("/api/auth/refresh", () => {
  it("returns 401 + clears cookies when refresh missing", async () => {
    mockCookieJar();
    vi.stubGlobal("fetch", vi.fn());

    const { POST } = await import("@/app/api/auth/refresh/route");
    const res = await POST();

    expect(res.status).toBe(401);
  });

  it("rotates cookies on upstream success", async () => {
    mockCookieJar({ [REFRESH_COOKIE]: "old-rfsh" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        fakeUpstream(200, { success: true }, [
          `${ACCESS_COOKIE}=new-access; Path=/`,
          `${REFRESH_COOKIE}=new-rfsh; Path=/`,
        ]),
      ),
    );

    const { POST } = await import("@/app/api/auth/refresh/route");
    const res = await POST();

    expect(res.status).toBe(200);
    const cookies = res.headers.get("set-cookie") ?? "";
    expect(cookies).toContain(`${ACCESS_COOKIE}=new-access`);
    expect(cookies).toContain(`${REFRESH_COOKIE}=new-rfsh`);
  });
});

describe("/api/auth/me", () => {
  it("returns 401 with no cookie", async () => {
    mockCookieJar();
    vi.stubGlobal("fetch", vi.fn());

    const { GET } = await import("@/app/api/auth/me/route");
    const res = await GET();

    expect(res.status).toBe(401);
  });

  it("returns the upstream session payload", async () => {
    mockCookieJar({ [ACCESS_COOKIE]: "jwt" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            sub: "u1",
            email: "u@test",
            display_name: "User",
            provider: "internal",
            active_org_id: "org-1",
            organization_id: "org-1",
            memberships: [{ organization_id: "org-1", role: "MEMBER", status: "active", joined_at: null }],
            roles: [],
            permissions: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const { GET } = await import("@/app/api/auth/me/route");
    const res = await GET();

    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({
      sub: "u1",
      active_org_id: "org-1",
      memberships: [expect.objectContaining({ organization_id: "org-1" })],
    });
  });
});
