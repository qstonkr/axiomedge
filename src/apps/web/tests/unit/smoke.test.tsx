import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import Home from "@/app/page";

describe("Home (B-1 Day 2 smoke)", () => {
  it("renders the bootstrap heading", () => {
    render(<Home />);
    expect(
      screen.getByRole("heading", { name: /Frontend MVP 부트스트랩 완료/ }),
    ).toBeInTheDocument();
  });

  it("uses design-token utility classes", () => {
    const { container } = render(<Home />);
    // Ensure tokenized classes are present — guards against accidental
    // re-introduction of hard-coded zinc/black colours from the scaffold.
    expect(container.innerHTML).toContain("bg-bg-canvas");
    expect(container.innerHTML).toContain("text-fg-default");
    expect(container.innerHTML).toContain("border-border-default");
  });
});
