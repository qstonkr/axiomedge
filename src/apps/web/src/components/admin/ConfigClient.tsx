"use client";

import { useMemo, useState } from "react";

import { Button, ErrorFallback, Input, Skeleton } from "@/components/ui";
import { useConfigWeights } from "@/hooks/admin/useOps";

type Leaf =
  | { kind: "number"; value: number; min: number; max: number; step: number }
  | { kind: "text"; value: string }
  | { kind: "bool"; value: boolean };

type FlatRow = { path: string; leaf: Leaf };

/**
 * 0.0 ~ 1.0 weight 값은 슬라이더 표시. 100 이상의 정수는 timeout/limit
 * 으로 추정해서 더 큰 max. 음수는 단순 number input.
 */
function classify(v: unknown): Leaf | null {
  if (typeof v === "number" && Number.isFinite(v)) {
    if (v >= 0 && v <= 1) {
      return { kind: "number", value: v, min: 0, max: 1, step: 0.01 };
    }
    if (Number.isInteger(v) && v >= 0 && v <= 1000) {
      return { kind: "number", value: v, min: 0, max: 1000, step: 1 };
    }
    if (v >= 0 && v <= 100) {
      return { kind: "number", value: v, min: 0, max: 100, step: 0.5 };
    }
    return null; // out-of-range — fall back to JSON dump
  }
  if (typeof v === "boolean") return { kind: "bool", value: v };
  if (typeof v === "string" && v.length < 80) return { kind: "text", value: v };
  return null;
}

function flatten(obj: unknown, prefix = ""): FlatRow[] {
  if (obj === null || typeof obj !== "object" || Array.isArray(obj)) {
    return [];
  }
  const out: FlatRow[] = [];
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${k}` : k;
    const leaf = classify(v);
    if (leaf) {
      out.push({ path, leaf });
    } else if (v && typeof v === "object" && !Array.isArray(v)) {
      out.push(...flatten(v, path));
    }
  }
  return out;
}

function ReadOnlyRow({ row }: { row: FlatRow }) {
  const { leaf } = row;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,260px)_60px] items-center gap-3 border-b border-border-default/60 px-3 py-2 text-xs">
      <span
        className="truncate font-mono text-fg-muted"
        title={row.path}
      >
        {row.path}
      </span>
      {leaf.kind === "number" ? (
        <input
          type="range"
          value={leaf.value}
          min={leaf.min}
          max={leaf.max}
          step={leaf.step}
          disabled
          aria-label={`${row.path} (read-only)`}
          className="h-1 w-full cursor-not-allowed accent-accent-default opacity-70"
        />
      ) : leaf.kind === "bool" ? (
        <span className="text-fg-default">
          {leaf.value ? "✅ true" : "❌ false"}
        </span>
      ) : (
        <span className="truncate text-fg-default" title={leaf.value}>
          {leaf.value}
        </span>
      )}
      <span className="text-right font-mono tabular-nums text-fg-default">
        {leaf.kind === "number"
          ? leaf.step < 1
            ? leaf.value.toFixed(2)
            : leaf.value
          : ""}
      </span>
    </div>
  );
}

export function ConfigClient() {
  const { data, isLoading, isError, error, refetch } = useConfigWeights();
  const [filter, setFilter] = useState("");
  const [showRaw, setShowRaw] = useState(false);

  const rows = useMemo(() => flatten(data ?? {}), [data]);
  const filtered = useMemo(() => {
    if (!filter.trim()) return rows;
    const q = filter.toLowerCase();
    return rows.filter((r) => r.path.toLowerCase().includes(q));
  }, [rows, filter]);

  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-fg-default">가중치 설정</h1>
          <p className="text-sm text-fg-muted">
            검색 파이프라인 가중치 + 임계값 — read-only 뷰. 0~1 범위 weight
            는 슬라이더로 시각화. 변경은{" "}
            <code className="font-mono text-xs">config/weights.yaml</code> 직접
            수정 + API restart.
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={() => setShowRaw((v) => !v)}>
          {showRaw ? "슬라이더 보기" : "원본 JSON 보기"}
        </Button>
      </header>

      {isLoading ? (
        <Skeleton className="h-96" />
      ) : isError ? (
        <ErrorFallback
          title="가중치를 불러올 수 없습니다"
          error={error}
          onRetry={() => refetch()}
        />
      ) : showRaw ? (
        <article className="rounded-lg border border-border-default bg-bg-canvas p-4">
          <pre className="max-h-[640px] overflow-auto font-mono text-[10px] leading-snug text-fg-default">
            {JSON.stringify(data, null, 2)}
          </pre>
        </article>
      ) : (
        <>
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="키 검색 (예: rerank, threshold, weight)"
          />
          <article className="overflow-hidden rounded-lg border border-border-default bg-bg-canvas">
            <header className="grid grid-cols-[minmax(0,1fr)_minmax(0,260px)_60px] gap-3 border-b border-border-default bg-bg-subtle px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-fg-muted">
              <span>키</span>
              <span>시각화</span>
              <span className="text-right">값</span>
            </header>
            {filtered.length === 0 ? (
              <p className="px-3 py-8 text-center text-xs text-fg-muted">
                일치하는 키 없음
              </p>
            ) : (
              filtered.map((row) => <ReadOnlyRow key={row.path} row={row} />)
            )}
          </article>
        </>
      )}
    </section>
  );
}
