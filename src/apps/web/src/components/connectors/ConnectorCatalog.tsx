"use client";

import { useMemo, useState } from "react";

import { Badge, Dialog, Input, cn } from "@/components/ui";
import {
  CATEGORY_LABELS,
  STATUS_BADGE,
  filterCatalog,
  groupByCategory,
  type ConnectorEntry,
  type ConnectorScope,
} from "@/lib/connectors/catalog";

type Props = {
  open: boolean;
  onClose: () => void;
  /** 사용자가 ``available``/``preview`` 카드 클릭 시 호출 (planned 는 비활성). */
  onSelect: (entry: ConnectorEntry) => void;
  scope: ConnectorScope;
  title?: string;
  description?: string;
  /** false 면 planned 카드 숨김 — 기본 true (로드맵 시각화). */
  showPlanned?: boolean;
};

export function ConnectorCatalog({
  open,
  onClose,
  onSelect,
  scope,
  title = "데이터 소스 선택",
  description = "추가할 connector 종류를 선택하세요. 회색 카드는 곧 출시 예정입니다.",
  showPlanned = true,
}: Props) {
  const [search, setSearch] = useState("");

  const groups = useMemo(
    () =>
      groupByCategory(filterCatalog({ scope, search, showPlanned })),
    [scope, search, showPlanned],
  );

  const totalAvailable = useMemo(
    () =>
      filterCatalog({ scope, showPlanned: false }).length,
    [scope],
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      width="xl"
    >
      <div className="space-y-5">
        <div className="space-y-2">
          <Input
            type="search"
            placeholder="🔍 소스 검색 — 이름 / 설명 (예: confluence, git, slack)"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          <p className="text-[11px] text-fg-subtle">
            현재 사용 가능: <strong>{totalAvailable}개</strong> · 로드맵 포함
            전체: <strong>{filterCatalog({ scope }).length}개</strong>
          </p>
        </div>

        {groups.length === 0 ? (
          <p className="py-12 text-center text-sm text-fg-muted">
            검색 결과가 없습니다.
          </p>
        ) : (
          <div className="space-y-5">
            {groups.map(({ category, items }) => (
              <section key={category} className="space-y-2">
                <h3 className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">
                  {CATEGORY_LABELS[category]}
                </h3>
                <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
                  {items.map((entry) => (
                    <ConnectorCard
                      key={entry.id}
                      entry={entry}
                      onSelect={onSelect}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </Dialog>
  );
}

function ConnectorCard({
  entry,
  onSelect,
}: {
  entry: ConnectorEntry;
  onSelect: (e: ConnectorEntry) => void;
}) {
  const badge = STATUS_BADGE[entry.status];
  const disabled = entry.status === "planned";

  return (
    <button
      type="button"
      onClick={() => !disabled && onSelect(entry)}
      disabled={disabled}
      className={cn(
        "group flex h-full flex-col gap-2 rounded-md border border-border-default bg-bg-default p-3 text-left transition",
        "hover:border-accent-default hover:shadow-sm",
        "disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:border-border-default disabled:hover:shadow-none",
      )}
      title={
        disabled
          ? "곧 출시 예정 — 백엔드 connector 구현 후 활성화"
          : `${entry.label} 추가`
      }
      aria-label={`${entry.label} — ${badge.label}`}
    >
      <div className="flex w-full items-start justify-between gap-2">
        <span className="text-2xl leading-none" aria-hidden>
          {entry.icon}
        </span>
        <Badge tone={badge.tone}>{badge.label}</Badge>
      </div>
      <div className="space-y-0.5">
        <div className="text-sm font-medium text-fg-default">{entry.label}</div>
        <p className="text-[11px] leading-snug text-fg-muted">
          {entry.description}
        </p>
      </div>
      {!disabled && (
        <span className="mt-auto text-[11px] font-medium text-accent-default group-hover:text-accent-strong">
          Select →
        </span>
      )}
    </button>
  );
}
