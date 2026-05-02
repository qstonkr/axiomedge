"use client";

import { useState, type FormEvent } from "react";

import { Badge, Button, Dialog, ErrorFallback, Input, Skeleton, Textarea, useToast } from "@/components/ui";
import {
  useCreateSearchGroup,
  useDeleteSearchGroup,
  useSearchGroups,
  useUpdateSearchGroup,
} from "@/hooks/admin/useContent";
import { useSearchableKbs } from "@/hooks/useSearch";
import type { SearchGroup, SearchGroupUpsertBody } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

export function GroupsClient() {
  const toast = useToast();
  const { data: kbs } = useSearchableKbs();
  const { data, isLoading, isError, error, refetch } = useSearchGroups();
  const create = useCreateSearchGroup();
  const update = useUpdateSearchGroup();
  const del = useDeleteSearchGroup();

  const [editing, setEditing] = useState<SearchGroup | null>(null);
  const [creating, setCreating] = useState(false);
  const groups = data?.groups ?? [];

  async function onDelete(g: SearchGroup) {
    if (g.is_default) {
      toast.push("기본 그룹은 삭제할 수 없습니다", "warning");
      return;
    }
    if (!confirm(`'${g.name}' 검색 그룹을 삭제하시겠습니까?`)) return;
    try {
      await del.mutateAsync(g.id);
      toast.push("삭제되었습니다", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    }
  }

  const columns: Column<SearchGroup>[] = [
    {
      key: "name",
      header: "그룹 이름",
      render: (g) => (
        <div className="flex items-center gap-2">
          <span className="font-medium text-fg-default">{g.name}</span>
          {g.is_default && <Badge tone="accent">기본</Badge>}
        </div>
      ),
    },
    {
      key: "description",
      header: "설명",
      render: (g) => (
        <span className="line-clamp-1 text-fg-muted">
          {g.description || "—"}
        </span>
      ),
    },
    {
      key: "kb_ids",
      header: "포함 KB",
      render: (g) => (
        <div className="flex flex-wrap gap-1">
          {(g.kb_ids ?? []).map((id) => (
            <span
              key={id}
              className="rounded bg-bg-muted px-1.5 py-0.5 font-mono text-[10px] text-fg-default"
            >
              {id}
            </span>
          ))}
          {(g.kb_ids ?? []).length === 0 && (
            <span className="text-fg-subtle">—</span>
          )}
        </div>
      ),
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (g) => (
        <div className="flex justify-end gap-1">
          <Button size="sm" variant="ghost" onClick={() => setEditing(g)}>
            수정
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={g.is_default}
            onClick={() => onDelete(g)}
          >
            삭제
          </Button>
        </div>
      ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="flex items-end justify-between">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-fg-default">검색 그룹</h1>
          <p className="text-sm text-fg-muted">
            여러 KB 를 묶어 한 번에 검색할 수 있는 사용자 친화 명칭.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          + 신규 그룹
        </Button>
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        <MetricCard label="총 그룹" value={groups.length} />
        <MetricCard
          label="기본 그룹"
          value={groups.filter((g) => g.is_default).length}
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <ErrorFallback
          title="검색 그룹을 불러올 수 없습니다"
          error={error}
          onRetry={() => refetch()}
        />
      ) : (
        <DataTable<SearchGroup>
          columns={columns}
          rows={groups}
          rowKey={(r) => r.id}
          empty="검색 그룹이 없습니다"
        />
      )}

      <GroupFormDialog
        key={editing?.id ?? (creating ? "create" : "closed")}
        open={creating || editing !== null}
        initial={editing}
        kbs={(kbs ?? []).map((k) => ({ kb_id: k.kb_id, name: k.name }))}
        onClose={() => {
          setCreating(false);
          setEditing(null);
        }}
        onSubmit={async (body) => {
          try {
            if (editing) {
              await update.mutateAsync({ id: editing.id, body });
              toast.push("수정되었습니다", "success");
            } else {
              await create.mutateAsync(body);
              toast.push("신규 그룹이 추가되었습니다", "success");
            }
            setCreating(false);
            setEditing(null);
          } catch (e) {
            toast.push(e instanceof Error ? e.message : "저장 실패", "danger");
          }
        }}
        pending={create.isPending || update.isPending}
      />
    </section>
  );
}

function GroupFormDialog({
  open,
  initial,
  kbs,
  onClose,
  onSubmit,
  pending,
}: {
  open: boolean;
  initial: SearchGroup | null;
  kbs: { kb_id: string; name: string }[];
  onClose: () => void;
  onSubmit: (body: SearchGroupUpsertBody) => void;
  pending: boolean;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [isDefault, setIsDefault] = useState(initial?.is_default ?? false);
  const [selectedKbIds, setSelectedKbIds] = useState<string[]>(
    initial?.kb_ids ?? [],
  );

  function toggle(kbId: string) {
    setSelectedKbIds((prev) =>
      prev.includes(kbId) ? prev.filter((id) => id !== kbId) : [...prev, kbId],
    );
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      name: name.trim(),
      kb_ids: selectedKbIds,
      description: description.trim(),
      is_default: isDefault,
    });
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={initial ? `검색 그룹 수정 — ${initial.name}` : "신규 검색 그룹"}
      description="이름 + 포함 KB 다중 선택 + (선택) 기본 그룹 여부"
      width="lg"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="group-form" disabled={pending || !name.trim() || selectedKbIds.length === 0}>
            {pending ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <form id="group-form" onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            이름
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoFocus
            />
          </label>
          <label className="flex items-center gap-2 pt-5 text-sm">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            <span className="text-fg-default">기본 그룹</span>
          </label>
        </div>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          설명
          <Textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
          />
        </label>
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-fg-muted">
            포함 KB ({selectedKbIds.length}개 선택됨)
          </p>
          <div className="grid max-h-48 gap-1 overflow-y-auto rounded border border-border-default bg-bg-canvas p-2 text-xs sm:grid-cols-2">
            {kbs.map((k) => {
              const checked = selectedKbIds.includes(k.kb_id);
              return (
                <label
                  key={k.kb_id}
                  className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-bg-muted"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(k.kb_id)}
                  />
                  <span className="font-mono text-fg-muted">{k.kb_id}</span>
                  <span className="truncate text-fg-default">{k.name}</span>
                </label>
              );
            })}
          </div>
        </div>
      </form>
    </Dialog>
  );
}
