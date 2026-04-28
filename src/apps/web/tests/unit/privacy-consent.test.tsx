import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PrivacyConsent } from "@/components/PrivacyConsent";

describe("PrivacyConsent", () => {
  beforeEach(() => {
    localStorage.removeItem("axe-privacy-consent-v1");
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response("{}", { status: 201 }) as unknown as Response,
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders modal on first visit, posts consent, and dismisses on accept", async () => {
    render(<PrivacyConsent />);
    expect(screen.getByRole("heading", { name: "처리방침 안내" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /동의/ }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "처리방침 안내" })).toBeNull(),
    );
    expect(localStorage.getItem("axe-privacy-consent-v1")).toBe("accepted");
    // Server-side trail — POST /users/me/consent
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/proxy/api/v1/users/me/consent"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("dismisses even if the network call fails (best-effort)", async () => {
    (global.fetch as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("network down"),
    );
    render(<PrivacyConsent />);
    await userEvent.setup().click(screen.getByRole("button", { name: /동의/ }));
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "처리방침 안내" })).toBeNull(),
    );
    expect(localStorage.getItem("axe-privacy-consent-v1")).toBe("accepted");
  });

  it("does not render when already accepted", () => {
    localStorage.setItem("axe-privacy-consent-v1", "accepted");
    render(<PrivacyConsent />);
    expect(screen.queryByRole("heading", { name: "처리방침 안내" })).toBeNull();
  });
});
