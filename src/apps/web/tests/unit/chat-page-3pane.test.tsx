import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ChatPage } from "@/components/chat/ChatPage";

vi.mock("@/store/conversations", () => ({
  useConversations: () => ({ data: [], isLoading: false }),
  useMessages: () => ({ data: [], isLoading: false }),
  useCreateConversation: () => ({
    mutateAsync: vi.fn().mockResolvedValue("c1"),
  }),
  useDeleteConversation: () => ({ mutateAsync: vi.fn() }),
  useRenameConversation: () => ({ mutateAsync: vi.fn() }),
  useSendMessage: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

function wrap() {
  const qc = new QueryClient();
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("ChatPage 3-pane", () => {
  it("renders left sidebar + center input + right source panel placeholders", () => {
    render(<ChatPage />, { wrapper: wrap() });
    expect(screen.getByText("+ 새 대화")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/질문/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /출처/ })).toBeInTheDocument();
  });
});
