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
    <div
      className={cn(
        "relative overflow-auto rounded-lg border border-border-default",
        // sticky thead 가 동작하려면 부모가 max-height + overflow-y 필요 (호출자가
        // 큰 list 일 때 max-h-* 추가 권장)
        className,
      )}
    >
      <table className="min-w-full divide-y divide-border-default text-xs">
        <thead className="sticky top-0 z-10 bg-bg-subtle text-fg-muted shadow-[0_1px_0_var(--color-border-default)]">
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={cn(
                  "px-2 py-2 text-left text-[11px] font-semibold uppercase tracking-wider",
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
        <tbody className="bg-bg-canvas">
          {rows.map((row, idx) => (
            <tr
              key={rowKey(row, idx)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn(
                "border-b border-border-default/60 leading-snug transition-colors",
                // zebra — 짝수 행 살짝 어둡게 (시선 추적)
                "even:bg-bg-subtle/40",
                // hover — 더 짙게 + 좌측 accent 살짝 비침 (interactive 시그널)
                "hover:bg-bg-muted/60",
                onRowClick &&
                  "cursor-pointer hover:shadow-[inset_2px_0_0_0_var(--color-accent-default)]",
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
