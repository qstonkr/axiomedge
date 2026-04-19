"use client";

import { useState, type FormEvent } from "react";

import {
  Button,
  Dialog,
  Input,
  Skeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import {
  useApproveGlossaryTerm,
  useCreateGlossaryTerm,
  useDeleteGlossaryTerm,
  useGlossary,
  useUpdateGlossaryTerm,
} from "@/hooks/admin/useContent";
import { useSearchableKbs } from "@/hooks/useSearch";
import type { GlossaryTerm, GlossaryUpsertBody } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

export function GlossaryClient() {
  const toast = useToast();
  const { data: kbs } = useSearchableKbs();
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<GlossaryTerm | null>(null);
  const [creating, setCreating] = useState(false);

  const create = useCreateGlossaryTerm();
  const update = useUpdateGlossaryTerm();
  const del = useDeleteGlossaryTerm();
  const approve = useApproveGlossaryTerm();

  const { data, isLoading, isError, error } = useGlossary({
    page: 1,
    page_size: 100,
  });

  const items = data?.items ?? [];
  const filtered = filter
    ? items.filter((t) =>
        (t.term + " " + (t.term_ko ?? "") + " " + (t.definition ?? ""))
          .toLowerCase()
          .includes(filter.toLowerCase()),
      )
    : items;

  async function onDelete(t: GlossaryTerm) {
    if (!confirm(`'${t.term}' 용어를 삭제하시겠습니까?`)) return;
    try {
      await del.mutateAsync(t.id);
      toast.push("삭제되었습니다", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    }
  }

  async function onApprove(t: GlossaryTerm) {
    try {
      await approve.mutateAsync(t.id);
      toast.push(`'${t.term}' 승인됨`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "승인 실패", "danger");
    }
  }

  const columns: Column<GlossaryTerm>[] = [
    {
      key: "term",
      header: "용어",
      render: (t) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg-default">{t.term}</span>
          {t.term_ko && (
            <span className="text-[10px] text-fg-subtle">{t.term_ko}</span>
          )}
        </div>
      ),
    },
    {
      key: "kb_id",
      header: "KB",
      render: (t) => (
        <span className="font-mono text-[10px] text-fg-muted">{t.kb_id}</span>
      ),
    },
    {
      key: "definition",
      header: "정의",
      render: (t) => (
        <span className="line-clamp-2 text-fg-default">
          {t.definition || "—"}
        </span>
      ),
    },
    {
      key: "domain",
      header: "도메인",
      render: (t) => (
        <span className="text-fg-muted">{t.domain || "—"}</span>
      ),
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (t) => (
        <div className="flex justify-end gap-1">
          {t.status === "pending" && (
            <Button size="sm" variant="ghost" onClick={() => onApprove(t)}>
              승인
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => setEditing(t)}>
            수정
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onDelete(t)}>
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
          <h1 className="text-xl font-semibold text-fg-default">용어집</h1>
          <p className="text-sm text-fg-muted">
            KB 별 용어 정의 + 동의어. 검색 시 query expansion 에 사용.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          + 신규 용어
        </Button>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <MetricCard label="총 용어" value={data?.total ?? 0} />
        <MetricCard label="필터 적용" value={filtered.length} />
        <div className="rounded-lg border border-border-default bg-bg-canvas p-4">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            검색
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="용어/정의 부분 일치"
            />
          </label>
        </div>
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            용어집을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<GlossaryTerm>
          columns={columns}
          rows={filtered}
          rowKey={(r) => r.id}
          empty={filter ? "검색 결과가 없습니다" : "등록된 용어가 없습니다"}
        />
      )}

      <GlossaryFormDialog
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
              toast.push("신규 용어가 추가되었습니다", "success");
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

function GlossaryFormDialog({
  open,
  initial,
  kbs,
  onClose,
  onSubmit,
  pending,
}: {
  open: boolean;
  initial: GlossaryTerm | null;
  kbs: { kb_id: string; name: string }[];
  onClose: () => void;
  onSubmit: (body: GlossaryUpsertBody) => void;
  pending: boolean;
}) {
  const [kbId, setKbId] = useState(initial?.kb_id ?? kbs[0]?.kb_id ?? "");
  const [term, setTerm] = useState(initial?.term ?? "");
  const [termKo, setTermKo] = useState(initial?.term_ko ?? "");
  const [definition, setDefinition] = useState(initial?.definition ?? "");
  const [synonyms, setSynonyms] = useState((initial?.synonyms ?? []).join(", "));
  const [domain, setDomain] = useState(initial?.domain ?? "");

  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      kb_id: kbId,
      term: term.trim(),
      term_ko: termKo.trim() || null,
      definition: definition.trim() || null,
      synonyms: synonyms
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      domain: domain.trim() || null,
    });
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={initial ? `용어 수정 — ${initial.term}` : "신규 용어"}
      description="용어 / 한국어 표기 / 정의 / 동의어 (콤마 구분) / 도메인"
      width="lg"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="glossary-form" disabled={pending || !term.trim() || !kbId}>
            {pending ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <form id="glossary-form" onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            KB
            <select
              value={kbId}
              onChange={(e) => setKbId(e.target.value)}
              className="block h-9 w-full rounded-md border border-border-default bg-bg-canvas px-2 text-sm text-fg-default focus:border-accent-default focus:outline-none"
              required
            >
              <option value="">— KB 선택 —</option>
              {kbs.map((k) => (
                <option key={k.kb_id} value={k.kb_id}>
                  {k.name} ({k.kb_id})
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            용어 (영문/원어)
            <Input
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              required
              autoFocus
            />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            한국어 표기
            <Input
              value={termKo}
              onChange={(e) => setTermKo(e.target.value)}
              placeholder="(선택)"
            />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            도메인
            <Input
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="(선택) 예: store, product, finance"
            />
          </label>
        </div>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          정의
          <Textarea
            value={definition}
            onChange={(e) => setDefinition(e.target.value)}
            rows={4}
          />
        </label>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          동의어 (콤마 구분)
          <Input
            value={synonyms}
            onChange={(e) => setSynonyms(e.target.value)}
            placeholder="예: ESPA, 에스파, Excellent Store Performance Aid"
          />
        </label>
      </form>
    </Dialog>
  );
}
