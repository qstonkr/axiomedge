import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { OwnerCard } from "@/components/find-owner/OwnerCard";

describe("OwnerCard", () => {
  it("renders name, team, expertise, contact, trust badge", () => {
    render(
      <OwnerCard
        owner={{
          id: "u1",
          name: "김담당",
          team: "CVS팀",
          expertise: ["PBU", "결제"],
          trust_score: 0.85,
          contact: "kim@example.com",
        }}
      />,
    );
    expect(screen.getByText("김담당")).toBeInTheDocument();
    expect(screen.getByText(/CVS팀/)).toBeInTheDocument();
    expect(screen.getByText(/PBU, 결제/)).toBeInTheDocument();
    expect(screen.getByText("kim@example.com")).toBeInTheDocument();
    expect(screen.getByText(/신뢰도 85%/)).toBeInTheDocument();
  });

  it("collapses documents into expander", () => {
    render(
      <OwnerCard
        owner={{
          id: "u2",
          name: "이담당",
          documents: [
            { title: "매뉴얼.pdf" },
            { title: "FAQ.md" },
          ],
        }}
      />,
    );
    expect(screen.getByText(/담당 문서 2개/)).toBeInTheDocument();
    expect(screen.getByText(/매뉴얼.pdf/)).toBeInTheDocument();
  });
});
