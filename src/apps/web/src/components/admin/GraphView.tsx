"use client";

import { useMemo, useState } from "react";

import { Button, Skeleton } from "@/components/ui";
import { useGraphExpand } from "@/hooks/admin/useOps";
import type { GraphNeighbor } from "@/lib/api/endpoints";

/**
 * 외부 라이브러리 없이 SVG 로 한 노드의 1-hop 이웃을 radial (hub-and-spoke)
 * 레이아웃으로 시각화. force-directed 가 아니지만 시각적 효과는 비슷
 * (관계 한눈에). 30+ 이웃은 잘림 — backend 가 max_neighbors 로 제한.
 */
const W = 640;
const H = 420;
const CX = W / 2;
const CY = H / 2;
const HUB_R = 28;
const NODE_R = 14;
const ORBIT = Math.min(W, H) / 2 - 50;

function neighborPos(i: number, total: number): { x: number; y: number } {
  const angle = (i / total) * Math.PI * 2 - Math.PI / 2;
  return { x: CX + Math.cos(angle) * ORBIT, y: CY + Math.sin(angle) * ORBIT };
}

function neighborKey(n: GraphNeighbor, idx: number): string {
  return n.id ?? n.node_id ?? `n-${idx}`;
}

function neighborLabel(n: GraphNeighbor): string {
  return (n.name ?? n.label ?? n.id ?? n.node_id ?? "?").toString();
}

function neighborTone(t: string | undefined): string {
  // entity_type 별 fill — accent palette 안에서 바리에이션.
  if (!t) return "var(--color-accent-default)";
  const k = t.toLowerCase();
  if (k.includes("person") || k.includes("expert"))
    return "var(--color-success-default)";
  if (k.includes("kb") || k.includes("group"))
    return "var(--color-warning-default)";
  if (k.includes("doc") || k.includes("chunk"))
    return "var(--color-accent-default)";
  return "var(--color-fg-muted)";
}

export function GraphView({
  nodeId,
  hubLabel,
}: {
  nodeId: string;
  hubLabel?: string;
}) {
  // 사용자가 이웃 노드 클릭 → 그 노드를 새 hub 로 (multi-hop 탐색).
  // history 로 이전 노드 스택 → 뒤로 가기 가능.
  const [activeNode, setActiveNode] = useState<{ id: string; label: string }>({
    id: nodeId,
    label: hubLabel ?? nodeId,
  });
  const [history, setHistory] = useState<{ id: string; label: string }[]>([]);

  const expand = useGraphExpand(activeNode.id);

  function focusNeighbor(n: GraphNeighbor) {
    const nextId =
      (n.id as string | undefined) ??
      (n.node_id as string | undefined) ??
      neighborLabel(n);
    if (!nextId || nextId === activeNode.id) return;
    setHistory((prev) => [...prev, activeNode]);
    setActiveNode({ id: nextId, label: neighborLabel(n) });
  }

  function goBack() {
    setHistory((prev) => {
      const last = prev[prev.length - 1];
      if (!last) return prev;
      setActiveNode(last);
      return prev.slice(0, -1);
    });
  }

  function reset() {
    setHistory([]);
    setActiveNode({ id: nodeId, label: hubLabel ?? nodeId });
  }

  const layout = useMemo(() => {
    const neighbors = (expand.data?.neighbors ?? []).slice(0, 24);
    const edges = expand.data?.edges ?? [];
    const positions = neighbors.map((n, idx) => ({
      key: neighborKey(n, idx),
      label: neighborLabel(n),
      type: n.entity_type ?? n.type,
      ...neighborPos(idx, Math.max(1, neighbors.length)),
      data: n,
    }));
    return { positions, edges };
  }, [expand.data]);

  if (expand.isLoading) return <Skeleton className="h-[420px]" />;

  if (!expand.data || (layout.positions.length === 0 && !expand.data.error)) {
    return (
      <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-8 text-center text-xs text-fg-muted">
        선택한 노드에 이웃이 없습니다.
      </p>
    );
  }

  if (expand.data.error) {
    return (
      <p className="rounded-md border border-danger-default/30 bg-danger-subtle px-4 py-3 text-xs text-danger-default">
        그래프 확장 실패: {expand.data.error}
      </p>
    );
  }

  return (
    <div className="rounded-lg border border-border-default bg-bg-canvas p-3">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2 text-fg-muted">
          <span>현재 hub:</span>
          <span className="font-medium text-fg-default">{activeNode.label}</span>
          {history.length > 0 && (
            <span className="text-fg-subtle">(depth {history.length + 1})</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={goBack}
            disabled={history.length === 0}
            title="이전 노드로 돌아가기"
          >
            ← 뒤로
          </Button>
          {history.length > 0 && (
            <Button size="sm" variant="ghost" onClick={reset}>
              처음으로
            </Button>
          )}
        </div>
      </header>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block w-full"
        role="img"
        aria-label={`${activeNode.label} 의 1-hop 이웃 그래프 (multi-hop 탐색 가능)`}
      >
        {/* edges (line from hub to each neighbor) */}
        {layout.positions.map((p) => (
          <line
            key={`e-${p.key}`}
            x1={CX}
            y1={CY}
            x2={p.x}
            y2={p.y}
            stroke="var(--color-border-strong)"
            strokeWidth={1}
            opacity={0.6}
          />
        ))}

        {/* hub node */}
        <g>
          <circle
            cx={CX}
            cy={CY}
            r={HUB_R}
            fill="var(--color-accent-default)"
            stroke="var(--color-accent-emphasis)"
            strokeWidth={2}
          />
          <text
            x={CX}
            y={CY + 4}
            textAnchor="middle"
            fontSize={11}
            fill="var(--color-fg-onAccent)"
            fontWeight={600}
          >
            {activeNode.label.slice(0, 12)}
          </text>
        </g>

        {/* neighbor nodes — 클릭하면 그 노드를 새 hub 로 (multi-hop). */}
        {layout.positions.map((p) => (
          <g
            key={`n-${p.key}`}
            onClick={() => focusNeighbor(p.data)}
            style={{ cursor: "pointer" }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                focusNeighbor(p.data);
              }
            }}
          >
            <title>{`${p.label} — 클릭하면 이 노드의 이웃을 봅니다`}</title>
            <circle
              cx={p.x}
              cy={p.y}
              r={NODE_R}
              fill={neighborTone(p.type)}
              stroke="var(--color-bg-canvas)"
              strokeWidth={2}
            />
            <text
              x={p.x}
              y={p.y + NODE_R + 12}
              textAnchor="middle"
              fontSize={10}
              fill="var(--color-fg-default)"
            >
              {p.label.slice(0, 14)}
            </text>
          </g>
        ))}
      </svg>

      <ul className="mt-3 grid gap-1 text-xs sm:grid-cols-2">
        {layout.positions.map((p) => (
          <li
            key={`l-${p.key}`}
            className="flex items-center gap-2 truncate text-fg-muted"
            title={`${p.label} · ${p.type ?? ""}`}
          >
            <span
              className="inline-block h-2 w-2 shrink-0 rounded-full"
              style={{ background: neighborTone(p.type) }}
            />
            <span className="truncate">{p.label}</span>
            {p.type && (
              <span className="shrink-0 font-mono text-[10px] text-fg-subtle">
                {p.type}
              </span>
            )}
          </li>
        ))}
      </ul>

      <p className="mt-2 text-[10px] text-fg-subtle">
        ※ 노드 클릭하면 그 노드를 새 hub 로 — multi-hop 탐색 가능 (← 뒤로
        버튼으로 복귀). 한 hop 당 24개까지 표시.
      </p>
    </div>
  );
}
