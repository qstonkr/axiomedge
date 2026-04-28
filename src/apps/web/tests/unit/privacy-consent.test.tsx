import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PrivacyConsent } from "@/components/PrivacyConsent";

describe("PrivacyConsent", () => {
  beforeEach(() => localStorage.removeItem("axe-privacy-consent-v1"));

  it("renders modal on first visit and dismisses on accept", async () => {
    render(<PrivacyConsent />);
    expect(screen.getByRole("heading", { name: "처리방침 안내" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /동의/ }));
    expect(screen.queryByRole("heading", { name: "처리방침 안내" })).toBeNull();
    expect(localStorage.getItem("axe-privacy-consent-v1")).toBe("accepted");
  });

  it("does not render when already accepted", () => {
    localStorage.setItem("axe-privacy-consent-v1", "accepted");
    render(<PrivacyConsent />);
    expect(screen.queryByRole("heading", { name: "처리방침 안내" })).toBeNull();
  });
});
