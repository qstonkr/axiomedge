/**
 * /my-knowledge view tests (B-1 Day 8).
 * KbCard renders KB metadata + delete confirms before firing the mutation.
 */
import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { KbCard } from "@/components/my-knowledge/KbCard";
import { ToastProvider } from "@/components/ui/Toast";

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>
  );
}

describe("KbCard", () => {
  it("renders name, kb_id, badge, document/chunk counts", () => {
    render(
      wrap(
        <KbCard
          userId="u1"
          selected={false}
          onSelect={() => undefined}
          kb={{
            kb_id: "pkb_u1_main",
            name: "내 첫 KB",
            description: "사이드 프로젝트 메모",
            tier: "personal",
            owner_id: "u1",
            document_count: 12,
            chunk_count: 234,
          }}
        />,
      ),
    );
    expect(screen.getByText("내 첫 KB")).toBeInTheDocument();
    expect(screen.getByText("pkb_u1_main")).toBeInTheDocument();
    expect(screen.getByText("개인")).toBeInTheDocument();
    expect(screen.getByText(/문서 12/)).toBeInTheDocument();
    expect(screen.getByText(/chunk 234/)).toBeInTheDocument();
  });

  it("delete button needs two clicks to confirm", () => {
    render(
      wrap(
        <KbCard
          userId="u1"
          selected={false}
          onSelect={() => undefined}
          kb={{
            kb_id: "pkb_u1_x",
            name: "X",
            tier: "personal",
            owner_id: "u1",
          }}
        />,
      ),
    );
    const btn = screen.getByRole("button", { name: "삭제" });
    fireEvent.click(btn);
    expect(
      screen.getByRole("button", { name: "정말 삭제?" }),
    ).toBeInTheDocument();
  });

  it("clicking the card body triggers onSelect", () => {
    let selected = false;
    render(
      wrap(
        <KbCard
          userId="u1"
          selected={false}
          onSelect={() => {
            selected = true;
          }}
          kb={{
            kb_id: "pkb_u1_y",
            name: "Y",
            tier: "personal",
            owner_id: "u1",
          }}
        />,
      ),
    );
    fireEvent.click(screen.getByText("Y"));
    expect(selected).toBe(true);
  });
});
