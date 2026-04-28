import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MessageActions } from "@/components/chat/MessageActions";

describe("MessageActions", () => {
  it("uses userEvent's clipboard for copy button", async () => {
    // userEvent.setup() installs its own clipboard implementation; assert via it.
    const u = userEvent.setup();
    render(
      <MessageActions
        content="답변"
        onReportError={() => {}}
        onResubmit={() => {}}
        onShowSources={() => {}}
        onFindOwner={() => {}}
      />
    );
    await u.click(screen.getByLabelText("복사"));
    const clipboardText = await navigator.clipboard.readText();
    expect(clipboardText).toBe("답변");
  });

  it("invokes onResubmit", async () => {
    const cb = vi.fn();
    render(
      <MessageActions
        content="x"
        onReportError={() => {}}
        onResubmit={cb}
        onShowSources={() => {}}
        onFindOwner={() => {}}
      />
    );
    await userEvent.setup().click(screen.getByLabelText("재질문"));
    expect(cb).toHaveBeenCalled();
  });
});
