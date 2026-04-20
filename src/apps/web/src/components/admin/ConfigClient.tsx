"use client";

import { useMemo, useState } from "react";

import {
  Button,
  ErrorFallback,
  Input,
  Skeleton,
  useToast,
} from "@/components/ui";
import {
  useConfigWeights,
  useResetConfigWeights,
  useUpdateConfigWeights,
} from "@/hooks/admin/useOps";

type Leaf =
  | { kind: "number"; value: number; min: number; max: number; step: number }
  | { kind: "text"; value: string }
  | { kind: "bool"; value: boolean };

type FlatRow = { path: string; leaf: Leaf };

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
    return null;
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

function leafValue(l: Leaf): number | string | boolean {
  if (l.kind === "number") return l.value;
  if (l.kind === "bool") return l.value;
  return l.value;
}

function EditableRow({
  row,
  staged,
  editMode,
  onChange,
}: {
  row: FlatRow;
  staged: Map<string, number | string | boolean>;
  editMode: boolean;
  onChange: (path: string, val: number | string | boolean) => void;
}) {
  const { leaf } = row;
  const cur = staged.has(row.path) ? staged.get(row.path)! : leafValue(leaf);
  const dirty = staged.has(row.path);

  return (
    <div
      className={`grid grid-cols-[minmax(0,1fr)_minmax(0,260px)_80px] items-center gap-3 border-b border-border-default/60 px-3 py-2 text-xs ${dirty ? "bg-warning-subtle/50" : ""}`}
    >
      <span
        className="truncate font-mono text-fg-muted"
        title={row.path}
      >
        {row.path}
        {dirty && (
          <span className="ml-1 text-[10px] font-semibold text-warning-default">
            ●
          </span>
        )}
      </span>
      {leaf.kind === "number" ? (
        <input
          type="range"
          value={Number(cur)}
          min={leaf.min}
          max={leaf.max}
          step={leaf.step}
          disabled={!editMode}
          aria-label={`${row.path}${editMode ? "" : " (read-only)"}`}
          onChange={(e) => onChange(row.path, Number(e.target.value))}
          className={`h-1 w-full accent-accent-default ${editMode ? "cursor-pointer" : "cursor-not-allowed opacity-70"}`}
        />
      ) : leaf.kind === "bool" ? (
        editMode ? (
          <input
            type="checkbox"
            checked={Boolean(cur)}
            onChange={(e) => onChange(row.path, e.target.checked)}
            className="h-4 w-4 accent-accent-default"
          />
        ) : (
          <span className="text-fg-default">{cur ? "✅ true" : "❌ false"}</span>
        )
      ) : editMode ? (
        <Input
          value={String(cur)}
          onChange={(e) => onChange(row.path, e.target.value)}
        />
      ) : (
        <span className="truncate text-fg-default" title={String(cur)}>
          {String(cur)}
        </span>
      )}
      <span className="text-right font-mono tabular-nums text-fg-default">
        {leaf.kind === "number"
          ? leaf.step < 1
            ? Number(cur).toFixed(2)
            : cur
          : ""}
      </span>
    </div>
  );
}

export function ConfigClient() {
  const toast = useToast();
  const { data, isLoading, isError, error, refetch } = useConfigWeights();
  const update = useUpdateConfigWeights();
  const reset = useResetConfigWeights();
  const [filter, setFilter] = useState("");
  const [showRaw, setShowRaw] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [staged, setStaged] = useState<
    Map<string, number | string | boolean>
  >(new Map());

  // staged clear 는 apply / discard / reset / 닫기 시 명시적으로만.
  // useEffect 안에서 setStaged 하면 React 19 Compiler 가 cascading rendering
  // 으로 잡으므로 (set-state-in-effect rule), 사용자 액션 기반 clear 만 함.

  const rows = useMemo(() => flatten(data ?? {}), [data]);
  const filtered = useMemo(() => {
    if (!filter.trim()) return rows;
    const q = filter.toLowerCase();
    return rows.filter((r) => r.path.toLowerCase().includes(q));
  }, [rows, filter]);

  function onChange(path: string, val: number | string | boolean) {
    setStaged((prev) => {
      const next = new Map(prev);
      next.set(path, val);
      return next;
    });
  }

  function discard() {
    setStaged(new Map());
  }

  async function apply() {
    if (staged.size === 0) return;
    const body: Record<string, unknown> = {};
    for (const [k, v] of staged) body[k] = v;
    try {
      const res = await update.mutateAsync(body);
      const applied = Object.keys(res.applied ?? {}).length;
      toast.push(`${applied}건 반영되었습니다`, "success");
      setStaged(new Map());
      setEditMode(false);
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "반영 실패", "danger");
    }
  }

  async function onReset() {
    if (!confirm("모든 가중치를 default 로 리셋하시겠습니까? (이전 설정 손실)"))
      return;
    try {
      await reset.mutateAsync();
      toast.push("default 로 리셋되었습니다", "success");
      setStaged(new Map());
      setEditMode(false);
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "리셋 실패", "danger");
    }
  }

  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-fg-default">가중치 설정</h1>
          <p className="text-sm text-fg-muted">
            검색 파이프라인 가중치 + 임계값. 수정 모드에서 슬라이더 변경 후
            &ldquo;반영&rdquo; 버튼으로 저장 (PUT /admin/config/weights). yaml
            직접 편집도 가능 (재시작 필요).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => setShowRaw((v) => !v)}>
            {showRaw ? "슬라이더 보기" : "원본 JSON 보기"}
          </Button>
          {!editMode ? (
            <Button size="sm" onClick={() => setEditMode(true)}>
              ✏️ 수정
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={discard}
                disabled={staged.size === 0}
              >
                되돌리기
              </Button>
              <Button
                size="sm"
                onClick={apply}
                disabled={staged.size === 0 || update.isPending}
              >
                {update.isPending
                  ? "반영 중…"
                  : `반영 (${staged.size}건)`}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={onReset}
                disabled={reset.isPending}
                title="default 로 전체 리셋"
              >
                🔄 리셋
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setEditMode(false);
                  discard();
                }}
              >
                닫기
              </Button>
            </>
          )}
        </div>
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
            <header className="grid grid-cols-[minmax(0,1fr)_minmax(0,260px)_80px] gap-3 border-b border-border-default bg-bg-subtle px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-fg-muted">
              <span>키</span>
              <span>{editMode ? "편집" : "시각화"}</span>
              <span className="text-right">값</span>
            </header>
            {filtered.length === 0 ? (
              <p className="px-3 py-8 text-center text-xs text-fg-muted">
                일치하는 키 없음
              </p>
            ) : (
              filtered.map((row) => (
                <EditableRow
                  key={row.path}
                  row={row}
                  staged={staged}
                  editMode={editMode}
                  onChange={onChange}
                />
              ))
            )}
          </article>
          {staged.size > 0 && (
            <p className="text-xs text-warning-default">
              ⚠️ {staged.size}건 변경 대기 중 — 반영 버튼 클릭 시 적용됩니다.
            </p>
          )}
        </>
      )}
    </section>
  );
}
