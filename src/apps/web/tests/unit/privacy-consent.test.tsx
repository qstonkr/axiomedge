import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PrivacyConsent } from "@/components/PrivacyConsent";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  }) as unknown as Response;
}

describe("PrivacyConsent", () => {
  beforeEach(() => {
    localStorage.removeItem("axe-privacy-consent-v1");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders modal when server has no consent record, posts on accept", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
      if (typeof init?.method === "undefined" || init.method === "GET") {
        return jsonResponse(null);
      }
      return jsonResponse({}, 201);
    });
    render(<PrivacyConsent />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "처리방침 안내" })).toBeInTheDocument(),
    );
    await userEvent.setup().click(screen.getByRole("button", { name: /동의/ }));
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "처리방침 안내" })).toBeNull(),
    );
    expect(localStorage.getItem("axe-privacy-consent-v1")).toBe("accepted");
    // GET (probe) + POST (accept)
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("re-prompts when server says consent is withdrawn even if localStorage is set", async () => {
    localStorage.setItem("axe-privacy-consent-v1", "accepted");
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({
        policy_version: "v1",
        accepted_at: "2026-01-01",
        withdrawn_at: "2026-04-01",
        is_active: false,
      }),
    );
    render(<PrivacyConsent />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "처리방침 안내" })).toBeInTheDocument(),
    );
    expect(localStorage.getItem("axe-privacy-consent-v1")).toBeNull();
  });

  it("hides modal when server confirms active consent", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({
        policy_version: "v1",
        accepted_at: "2026-01-01",
        withdrawn_at: null,
        is_active: true,
      }),
    );
    render(<PrivacyConsent />);
    await waitFor(() =>
      expect(localStorage.getItem("axe-privacy-consent-v1")).toBe("accepted"),
    );
    expect(screen.queryByRole("heading", { name: "처리방침 안내" })).toBeNull();
  });

  it("dismisses even if the network call fails (best-effort)", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (_url, init) => {
      if (typeof init?.method === "undefined" || init.method === "GET") {
        return jsonResponse(null);
      }
      throw new Error("network down");
    });
    render(<PrivacyConsent />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "처리방침 안내" })).toBeInTheDocument(),
    );
    await userEvent.setup().click(screen.getByRole("button", { name: /동의/ }));
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "처리방침 안내" })).toBeNull(),
    );
    expect(localStorage.getItem("axe-privacy-consent-v1")).toBe("accepted");
  });
});
