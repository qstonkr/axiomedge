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

  it("collapses to narrow icon rail on /chat (B1 — always-on outer nav)", () => {
    mockedPathname = "/chat";
    const { container } = render(<Sidebar />);
    // 이전 동작: return null. 새 동작: render 하되 collapsed (w-14) 으로 nav 항상 노출.
    const aside = container.querySelector("aside");
    expect(aside).not.toBeNull();
    expect(aside?.className).toContain("w-14");
    // 라벨 텍스트는 hidden, 아이콘만 노출 (collapsed = !collapsed condition false)
    expect(screen.queryByText("chat")).toBeNull();
    // 그러나 chat link 는 여전히 존재 (icon-only)
    expect(screen.getByRole("link", { name: /chat/i })).toBeInTheDocument();
  });
});
