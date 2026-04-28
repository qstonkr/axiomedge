/**
 * /chat page-level smoke tests (B-1 Day 5).
 * - SourceCard renders tier badge + score + content
 * - MetaSignals filters out missing fields
 * - ChatInput dispatches on submit, ⌘/Ctrl+Enter
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ChatInput } from "@/components/chat/ChatInput";
import { MetaSignals } from "@/components/chat/MetaSignals";
import { SourceCard } from "@/components/chat/SourceCard";

describe("SourceCard", () => {
  it("shows title, tier badge, score and text", () => {
    render(
      <SourceCard
        chunk={{
          id: "c1",
          kb_id: "kb_g_espa",
          document_name: "신촌점 매뉴얼.pdf",
          tier: "team",
          rerank_score: 0.84,
          text: "차주 매장 점검 일정은 매주 월요일 오전 10시…",
        }}
      />,
    );
    expect(screen.getByText("신촌점 매뉴얼.pdf")).toBeInTheDocument();
    expect(screen.getByText("team")).toBeInTheDocument();
    expect(screen.getByText("0.84")).toBeInTheDocument();
  });

  it("invokes onReportError when '오류 신고' clicked", () => {
    const onReportError = vi.fn();
    render(
      <SourceCard
        chunk={{ id: "c1", kb_id: "kb_x" }}
        onReportError={onReportError}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "오류 신고" }));
    expect(onReportError).toHaveBeenCalledOnce();
  });
});

describe("MetaSignals", () => {
  it("renders only fields that are present", () => {
    const { container } = render(
      <MetaSignals
        meta={{
          confidence: 0.72,
          llm_provider: "ollama",
          search_time_ms: 1200,
        }}
      />,
    );
    expect(container.textContent).toContain("신뢰도");
    expect(container.textContent).toContain("72%");
    expect(container.textContent).toContain("LLM");
    expect(container.textContent).toContain("ollama");
    expect(container.textContent).toContain("응답");
    expect(container.textContent).not.toContain("CRAG");
  });

  it("returns null when meta is undefined", () => {
    const { container } = render(<MetaSignals meta={undefined} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("ChatInput", () => {
  it("does nothing on empty submit", () => {
    const onSubmit = vi.fn();
    render(<ChatInput onSubmit={onSubmit} pending={false} />);
    const ta = screen.getByPlaceholderText(/질문/) as HTMLTextAreaElement;
    fireEvent.keyDown(ta, { key: "Enter", metaKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("⌘+Enter sends and clears", () => {
    const onSubmit = vi.fn();
    render(<ChatInput onSubmit={onSubmit} pending={false} />);
    const ta = screen.getByPlaceholderText(/질문/) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "테스트" } });
    fireEvent.keyDown(ta, { key: "Enter", metaKey: true });
    expect(onSubmit).toHaveBeenCalledWith("테스트");
    expect(ta.value).toBe("");
  });
});
