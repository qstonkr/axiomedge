import { describe, expect, it, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";

import { ChatMessages } from "@/components/chat/ChatMessages";
import type { ChatMessage } from "@/lib/api/chat";

const noop = () => {};

const empty: ChatMessage[] = [];

describe("ChatMessages — pending UI", () => {
  it("renders the optimistic user turn + assistant skeleton when pendingQuery is set", () => {
    render(
      <ChatMessages
        messages={empty}
        pendingQuery="GS25 세마역점 분쟁?"
        onMarkerActivate={noop}
        onMarkerDeactivate={noop}
        onReportError={noop}
        onResubmit={noop}
        onFindOwner={noop}
      />,
    );
    expect(screen.getByText("GS25 세마역점 분쟁?")).toBeInTheDocument();
    // Initial elapsed-time hint
    expect(screen.getByText(/답변을 생성하고/)).toBeInTheDocument();
    // aria-busy lets assistive tech know something is in flight
    expect(screen.getByRole("listitem", { busy: true })).toBeInTheDocument();
  });

  it("escalates the hint after ~5s of waiting", () => {
    vi.useFakeTimers();
    render(
      <ChatMessages
        messages={empty}
        pendingQuery="hi"
        onMarkerActivate={noop}
        onMarkerDeactivate={noop}
        onReportError={noop}
        onResubmit={noop}
        onFindOwner={noop}
      />,
    );
    act(() => {
      vi.advanceTimersByTime(6000);
    });
    expect(screen.getByText(/관련 문서를 찾는 중/)).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("renders nothing extra when pendingQuery is null", () => {
    render(
      <ChatMessages
        messages={empty}
        pendingQuery={null}
        onMarkerActivate={noop}
        onMarkerDeactivate={noop}
        onReportError={noop}
        onResubmit={noop}
        onFindOwner={noop}
      />,
    );
    expect(screen.queryByRole("listitem", { busy: true })).toBeNull();
    expect(screen.queryByText(/답변을 생성/)).toBeNull();
  });
});
