import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ModeForceMenu } from "@/components/chat/ModeForceMenu";

describe("ModeForceMenu", () => {
  it("toggles between auto/quick/deep", async () => {
    const cb = vi.fn();
    render(<ModeForceMenu value="auto" onChange={cb} />);
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: /고급/i }));
    await u.click(screen.getByRole("menuitem", { name: /빠른/ }));
    expect(cb).toHaveBeenCalledWith("quick");
  });
});
