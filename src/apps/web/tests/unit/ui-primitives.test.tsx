/**
 * Smoke tests for the UI primitive set (B-1 Day 4).
 * These don't aim to spec every variant — just to assert the components
 * mount, the design-token classes survive, and a11y wiring works.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tabs } from "@/components/ui/Tabs";
import { Textarea } from "@/components/ui/Textarea";

describe("Button", () => {
  it("renders children and fires onClick", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>저장</Button>);
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("applies variant tokens", () => {
    render(<Button variant="danger">삭제</Button>);
    expect(screen.getByRole("button").className).toContain("bg-danger-default");
  });
});

describe("Card", () => {
  it("composes Header/Title/Body", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>제목</CardTitle>
        </CardHeader>
        <CardBody>본문</CardBody>
      </Card>,
    );
    expect(screen.getByRole("heading", { name: "제목" })).toBeInTheDocument();
    expect(screen.getByText("본문")).toBeInTheDocument();
  });
});

describe("Input + Textarea + Select", () => {
  it("Input invalid sets aria-invalid", () => {
    render(<Input invalid placeholder="email" />);
    expect(screen.getByPlaceholderText("email")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("Textarea forwards value", () => {
    const onChange = vi.fn();
    render(<Textarea value="" onChange={onChange} placeholder="내용" />);
    fireEvent.change(screen.getByPlaceholderText("내용"), {
      target: { value: "hello" },
    });
    expect(onChange).toHaveBeenCalled();
  });

  it("Select renders options", () => {
    render(
      <Select aria-label="role">
        <option value="a">A</option>
        <option value="b">B</option>
      </Select>,
    );
    expect(screen.getByRole("combobox", { name: "role" })).toBeInTheDocument();
  });
});

describe("Tabs", () => {
  it("switches panel on click", () => {
    render(
      <Tabs
        items={[
          { id: "x", label: "X", content: <div>panel-x</div> },
          { id: "y", label: "Y", content: <div>panel-y</div> },
        ]}
      />,
    );
    expect(screen.getByText("panel-x")).toBeInTheDocument();
    expect(screen.queryByText("panel-y")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Y" }));
    expect(screen.getByText("panel-y")).toBeInTheDocument();
  });

  it("sets aria-selected on active tab", () => {
    render(
      <Tabs
        items={[
          { id: "x", label: "X", content: null },
          { id: "y", label: "Y", content: null },
        ]}
        defaultActiveId="y"
      />,
    );
    expect(screen.getByRole("tab", { name: "Y" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

describe("Badge / Skeleton / EmptyState", () => {
  it("Badge tone token applied", () => {
    render(<Badge tone="success">OK</Badge>);
    expect(screen.getByText("OK").className).toContain("bg-success-subtle");
  });

  it("Skeleton has aria-hidden", () => {
    const { container } = render(<Skeleton className="h-4 w-20" />);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
  });

  it("EmptyState shows title + action", () => {
    render(
      <EmptyState
        title="비어 있음"
        description="추가해 보세요."
        action={<Button>추가</Button>}
      />,
    );
    // EmptyState 는 heading 이 아니라 status (role=status) — 페이지 위계
    // (h1→h3 점프) axe-core heading-order 위반 없애고 "정보 없음" 안내라는
    // 의미를 그대로 살리려 변경.
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("비어 있음");
    expect(screen.getByRole("button", { name: "추가" })).toBeInTheDocument();
  });
});
