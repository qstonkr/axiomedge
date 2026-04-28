import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

import { Sidebar } from "@/components/layout/Sidebar";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

let mockedPathname = "/my-knowledge";
vi.mock("next/navigation", async (orig) => {
  const actual = await orig<typeof import("next/navigation")>();
  return {
    ...actual,
    usePathname: () => mockedPathname,
  };
});

describe("Sidebar nav (trimmed)", () => {
  beforeEach(() => {
    mockedPathname = "/my-knowledge";
  });

  it("does not render search-history or find-owner links", () => {
    render(<Sidebar />);
    expect(screen.queryByRole("link", { name: /search_history/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /find_owner/i })).toBeNull();
  });

  it("does not render my-feedback or my-activities (moved to profile)", () => {
    render(<Sidebar />);
    expect(screen.queryByRole("link", { name: /my_feedback/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /my_activities/i })).toBeNull();
  });

  it("returns nothing on /chat (chat owns its own sidebar)", () => {
    mockedPathname = "/chat";
    const { container } = render(<Sidebar />);
    expect(container.firstChild).toBeNull();
  });
});
