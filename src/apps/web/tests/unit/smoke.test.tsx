import { describe, expect, it, vi } from "vitest";

// next/navigation's `redirect` throws a special error to halt rendering.
vi.mock("next/navigation", () => ({
  redirect: vi.fn((path: string) => {
    throw new Error(`NEXT_REDIRECT:${path}`);
  }),
}));

vi.mock("@/lib/auth/session", () => ({
  getSession: vi.fn(),
}));

import Home from "@/app/page";
import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth/session";

describe("Home (B-1 root redirect)", () => {
  it("sends logged-out users to /login", async () => {
    (getSession as ReturnType<typeof vi.fn>).mockResolvedValueOnce(null);
    await expect(Home()).rejects.toThrow(/NEXT_REDIRECT:\/login/);
    expect(redirect).toHaveBeenCalledWith("/login");
  });

  it("sends logged-in users to /chat", async () => {
    (getSession as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      sub: "u1",
      email: "u@test",
      display_name: "U",
      provider: "internal",
      active_org_id: "org-1",
      organization_id: "org-1",
      memberships: [],
      roles: [],
      permissions: [],
    });
    await expect(Home()).rejects.toThrow(/NEXT_REDIRECT:\/chat/);
    expect(redirect).toHaveBeenCalledWith("/chat");
  });
});
