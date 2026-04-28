import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CitationMarker } from "@/components/chat/CitationMarker";

describe("CitationMarker", () => {
  it("renders [N]", () => {
    render(<CitationMarker n={3} onActivate={() => {}} />);
    expect(screen.getByText("[3]")).toBeInTheDocument();
  });

  it("calls onActivate on click", async () => {
    const cb = vi.fn();
    render(<CitationMarker n={1} onActivate={cb} />);
    await userEvent.setup().click(screen.getByText("[1]"));
    expect(cb).toHaveBeenCalledWith(1);
  });
});
