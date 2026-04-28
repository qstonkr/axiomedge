import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SourcePanel } from "@/components/chat/SourcePanel";

const sources = [
  {
    chunk_id: "c1",
    marker: 1,
    doc_title: "정책 v3.2",
    kb_id: "g-espa",
    snippet: "본문 발췌…",
    score: 0.9,
    owner: "김철수",
  },
  {
    chunk_id: "c2",
    marker: 2,
    doc_title: "회의록",
    kb_id: "g-espa",
    snippet: "회의 본문…",
    score: 0.7,
    owner: null,
  },
];

describe("SourcePanel", () => {
  it("renders source cards", () => {
    render(<SourcePanel chunks={sources} meta={{}} highlightedMarker={null} />);
    expect(screen.getByText("정책 v3.2")).toBeInTheDocument();
    expect(screen.getByText("회의록")).toBeInTheDocument();
  });

  it("switches to meta tab", async () => {
    render(<SourcePanel chunks={sources} meta={{ confidence: 0.78 }} highlightedMarker={null} />);
    const u = userEvent.setup();
    await u.click(screen.getByRole("tab", { name: /메타/i }));
    expect(screen.getByText(/0\.78/)).toBeInTheDocument();
  });

  it("highlights card when marker matches", () => {
    render(<SourcePanel chunks={sources} meta={{}} highlightedMarker={2} />);
    const card = screen.getByText("회의록").closest("[data-marker]");
    expect(card?.getAttribute("data-highlighted")).toBe("true");
  });
});
