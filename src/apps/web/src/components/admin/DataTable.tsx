"use client";

import type { ReactNode } from "react";

import { cn } from "@/components/ui/cn";

export type Column<Row> = {
  key: string;
  header: ReactNode;
  /** Cell renderer — 기본은 row[key] 그대로. */
  render?: (row: Row) => ReactNode;
  /** "text-right" 등 추가 td className */
  align?: "left" | "right" | "center";
  /** th/td 의 폭 (Tailwind w-* 클래스 또는 raw style) */
  width?: string;
  /** sticky col (예: ID 컬럼) — 모바일에서 가로 스크롤 시 고정 */
  sticky?: boolean;
};

export function DataTable<Row extends Record<string, unknown>>({
  columns,
  rows,
  rowKey,
  empty,
  onRowClick,
  className,
}: {
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row, idx: number) => string;
  empty?: ReactNode;
  onRowClick?: (row: Row) => void;
  className?: string;
}) {
  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border-default bg-bg-subtle px-6 py-10 text-center text-sm text-fg-muted">
        {empty ?? "데이터가 없습니다"}
      </div>
    );
  }

  return (
    <div className={cn("overflow-x-auto rounded-lg border border-border-default", className)}>
      <table className="min-w-full divide-y divide-border-default text-xs">
        <thead className="bg-bg-subtle text-fg-muted">
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={cn(
                  "px-2 py-1.5 text-left font-medium",
                  c.align === "right" && "text-right",
                  c.align === "center" && "text-center",
                  c.sticky && "sticky left-0 bg-bg-subtle",
                )}
                style={c.width ? { width: c.width } : undefined}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border-default bg-bg-canvas">
          {rows.map((row, idx) => (
            <tr
              key={rowKey(row, idx)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn(
                "leading-snug",
                onRowClick && "cursor-pointer transition-colors hover:bg-bg-muted/50",
              )}
            >
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={cn(
                    "px-2 py-1.5 text-fg-default",
                    c.align === "right" && "text-right tabular-nums",
                    c.align === "center" && "text-center",
                    c.sticky && "sticky left-0 bg-bg-canvas",
                  )}
                >
                  {c.render ? c.render(row) : (row[c.key] as ReactNode) ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
