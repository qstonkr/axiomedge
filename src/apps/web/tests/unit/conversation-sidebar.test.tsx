import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ConversationSidebar } from "@/components/chat/ConversationSidebar";

vi.mock("@/store/conversations", () => ({
  useConversations: () => ({
    data: [
      {
        id: "c1",
        title: "신촌 점검",
        kb_ids: [],
        updated_at: new Date().toISOString(),
      },
      {
        id: "c2",
        title: "MD 업무",
        kb_ids: [],
        updated_at: new Date(Date.now() - 86400000).toISOString(),
      },
    ],
    isLoading: false,
  }),
  useDeleteConversation: () => ({ mutateAsync: vi.fn() }),
  useRenameConversation: () => ({ mutateAsync: vi.fn() }),
  useCreateConversation: () => ({ mutateAsync: vi.fn().mockResolvedValue("new-id") }),
}));

function wrap() {
  const qc = new QueryClient();
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("ConversationSidebar", () => {
  it("groups by 오늘/어제", () => {
    render(<ConversationSidebar activeId={null} />, { wrapper: wrap() });
    expect(screen.getByText("오늘")).toBeInTheDocument();
    expect(screen.getByText("어제")).toBeInTheDocument();
  });

  it("filters by search box", async () => {
    render(<ConversationSidebar activeId={null} />, { wrapper: wrap() });
    const u = userEvent.setup();
    await u.type(screen.getByRole("searchbox"), "신촌");
    expect(screen.getByText("신촌 점검")).toBeInTheDocument();
    expect(screen.queryByText("MD 업무")).not.toBeInTheDocument();
  });

  it("calls onSelect when item clicked", async () => {
    const onSelect = vi.fn();
    render(<ConversationSidebar activeId={null} onSelect={onSelect} />, {
      wrapper: wrap(),
    });
    const u = userEvent.setup();
    await u.click(screen.getByText("신촌 점검"));
    expect(onSelect).toHaveBeenCalledWith("c1");
  });
});
