import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProfileDropdown } from "@/components/layout/ProfileDropdown";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ProfileDropdown", () => {
  it("opens menu and shows feedback/activities/policy/withdraw/logout", async () => {
    render(<ProfileDropdown email="x@y.com" />);
    await userEvent.setup().click(screen.getByRole("button", { name: /프로필/i }));
    expect(screen.getByRole("menuitem", { name: /피드백/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /활동/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /처리방침$/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /동의 철회/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /로그아웃/ })).toBeInTheDocument();
  });

  it("withdraws consent on confirm — DELETE + clear localStorage + reload", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const reloadSpy = vi.fn();
    Object.defineProperty(window, "location", {
      value: { reload: reloadSpy, href: "/", origin: "http://localhost" },
      configurable: true,
    });
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response("{}", { status: 200 }) as unknown as Response);
    localStorage.setItem("axe-privacy-consent-v1", "accepted");

    render(<ProfileDropdown email="x@y.com" />);
    await userEvent.setup().click(screen.getByRole("button", { name: /프로필/i }));
    await userEvent.setup().click(screen.getByRole("menuitem", { name: /동의 철회/ }));

    await waitFor(() => expect(reloadSpy).toHaveBeenCalled());
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining("/api/proxy/api/v1/users/me/consent"),
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(localStorage.getItem("axe-privacy-consent-v1")).toBeNull();
  });

  it("does nothing when user cancels the confirm dialog", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchSpy = vi.spyOn(global, "fetch");
    render(<ProfileDropdown email="x@y.com" />);
    await userEvent.setup().click(screen.getByRole("button", { name: /프로필/i }));
    await userEvent.setup().click(screen.getByRole("menuitem", { name: /동의 철회/ }));
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
