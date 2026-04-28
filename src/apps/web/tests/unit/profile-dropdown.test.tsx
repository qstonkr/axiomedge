import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProfileDropdown } from "@/components/layout/ProfileDropdown";

describe("ProfileDropdown", () => {
  it("opens menu and shows feedback/activities/policy/logout", async () => {
    render(<ProfileDropdown email="x@y.com" />);
    await userEvent.setup().click(screen.getByRole("button", { name: /프로필/i }));
    expect(screen.getByRole("menuitem", { name: /피드백/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /활동/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /처리방침/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /로그아웃/ })).toBeInTheDocument();
  });
});
