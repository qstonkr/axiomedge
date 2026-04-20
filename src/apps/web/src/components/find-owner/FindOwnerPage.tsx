"use client";

import { useState, type FormEvent } from "react";

import {
  Button,
  EmptyState,
  Input,
  Select,
  Skeleton,
} from "@/components/ui";
import { useSearchableKbs } from "@/hooks/useSearch";
import { useOwnerSearch } from "@/hooks/useOwners";

import { OwnerCard } from "./OwnerCard";

export function FindOwnerPage() {
  const [draft, setDraft] = useState("");
  const [committed, setCommitted] = useState({ query: "", kb_id: undefined as string | undefined });
  const { data: kbs } = useSearchableKbs();
  const { data, isFetching, isError } = useOwnerSearch(committed);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setCommitted({ query: draft.trim(), kb_id: committed.kb_id || undefined });
  }

  function pickExample(q: string) {
    setDraft(q);
    setCommitted({ query: q, kb_id: committed.kb_id || undefined });
  }

  const owners = data?.owners ?? [];
  const partialErrors = data?.partial_errors ?? [];

  return (
    <section className="mx-auto w-full max-w-4xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold leading-snug text-fg-default">
          👤 담당자 찾기
        </h1>
        <p className="text-sm text-fg-muted">
          시스템/도메인 키워드로 담당자를 찾을 수 있습니다.
        </p>
      </header>

      <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-3">
        <label className="block flex-1 space-y-1 text-xs font-medium text-fg-muted">
          검색어
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="예: PBU 담당, 신촌점 점장…"
            autoFocus
          />
        </label>
        <label className="block w-48 space-y-1 text-xs font-medium text-fg-muted">
          KB 필터
          <Select
            value={committed.kb_id ?? ""}
            onChange={(e) =>
              setCommitted((prev) => ({
                query: prev.query,
                kb_id: e.target.value || undefined,
              }))
            }
          >
            <option value="">전체</option>
            {(kbs ?? []).map((kb) => (
              <option key={kb.kb_id} value={kb.kb_id}>
                {kb.name}
              </option>
            ))}
          </Select>
        </label>
        <Button type="submit" disabled={!draft.trim()}>
          검색
        </Button>
      </form>

      {committed.query === "" && (
        <EmptyState
          icon="👀"
          title="검색어를 입력해 보세요"
          description="역할/도메인/시스템 이름으로 가장 잘 찾을 수 있습니다."
          action={
            <div className="flex flex-wrap gap-2">
              {["PBU 담당", "신촌점 점장", "결제 시스템"].map((s) => (
                <Button key={s} size="sm" variant="ghost" onClick={() => pickExample(s)}>
                  {s}
                </Button>
              ))}
            </div>
          }
        />
      )}

      {committed.query !== "" && isFetching && (
        <div className="grid gap-3 sm:grid-cols-2">
          {Array.from({ length: 4 }).map((_, idx) => (
            <Skeleton key={idx} className="h-32" />
          ))}
        </div>
      )}

      {committed.query !== "" && !isFetching && isError && (
        <EmptyState
          icon="⚠️"
          title="검색에 실패했습니다"
          description="잠시 후 다시 시도해 주세요."
        />
      )}

      {committed.query !== "" && !isFetching && !isError && owners.length === 0 && (
        <EmptyState
          icon="🤷"
          title="결과가 없습니다"
          description="다른 키워드로 검색해 보세요."
        />
      )}

      {/* 부분 실패 — 일부 검색 source(graph/document_owner 등)가 실패해도
          나머지 결과는 표시. 어떤 source 가 실패했는지 디버깅용으로 노출. */}
      {partialErrors.length > 0 && (
        <details className="rounded-md border border-warning-default/30 bg-warning-subtle px-3 py-2 text-xs">
          <summary className="cursor-pointer font-medium text-warning-default">
            일부 검색 소스 실패 ({partialErrors.length}건) — 결과가 부분적일 수 있습니다
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 font-mono text-fg-muted">
            {partialErrors.map((e, i) => (
              <li key={i} className="break-words">
                {e}
              </li>
            ))}
          </ul>
        </details>
      )}

      {owners.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {owners.map((o) => (
            <OwnerCard key={o.id} owner={o} />
          ))}
        </div>
      )}
    </section>
  );
}
