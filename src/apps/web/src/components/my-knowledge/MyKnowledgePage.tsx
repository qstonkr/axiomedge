"use client";

import { useState } from "react";
import { BookOpen, Download, FolderOpen } from "lucide-react";

import { ConnectorCatalog } from "@/components/connectors/ConnectorCatalog";
import {
  Button,
  EmptyState,
  ErrorFallback,
  Skeleton,
  useToast,
} from "@/components/ui";
import { useKbDocuments, useMyPersonalKbs } from "@/hooks/useMyKnowledge";
import type { ConnectorEntry } from "@/lib/connectors/catalog";

import { CreateKbDialog } from "./CreateKbDialog";
import { DocumentList } from "./DocumentList";
import { DocumentUploader } from "./DocumentUploader";
import { KbCard } from "./KbCard";
import { UserDataSourceDialog } from "./UserDataSourceDialog";

const PERSONAL_KB_LIMIT = 10;

export function MyKnowledgePage({ userId }: { userId: string }) {
  const toast = useToast();
  const { data, isLoading, isError, error, refetch } = useMyPersonalKbs(userId);
  const [creating, setCreating] = useState(false);
  const [selectedKbId, setSelectedKbId] = useState<string | null>(null);
  // 카탈로그 dialog: 사용자가 connector 종류를 카드 grid 에서 선택.
  // file_upload → 기존 DocumentUploader 영역으로 scroll. per-user/shared
  // 토큰 모드 connector → UserDataSourceDialog 로 self-service 등록.
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [pickedConnector, setPickedConnector] = useState<ConnectorEntry | null>(
    null,
  );

  const kbs = data ?? [];
  const selected = kbs.find((kb) => kb.kb_id === selectedKbId) ?? kbs[0];
  const atCap = kbs.length >= PERSONAL_KB_LIMIT;
  const docs = useKbDocuments(selected?.kb_id, { page: 1, page_size: 50 });

  function onPickConnector(entry: ConnectorEntry) {
    setCatalogOpen(false);
    if (entry.id === "file_upload") {
      // 현재 화면 안의 DocumentUploader 영역으로 scroll — 사용자가 즉시 업로드 가능.
      const el = document.getElementById("personal-uploader");
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      el?.focus();
      return;
    }
    // per-user / shared token connector 는 사용자 form dialog 로.
    if (entry.userTokenMode === "per-user" || entry.userTokenMode === "shared") {
      setPickedConnector(entry);
      return;
    }
    // 그 외 (admin-only / planned) — 안내.
    toast.push(
      `${entry.label} 임포트는 현재 관리자 권한이 필요합니다 — 관리자에게 요청해주세요.`,
      "warning",
    );
  }

  return (
    <section className="mx-auto w-full max-w-5xl space-y-6 px-6 py-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-2">
          <h1 className="flex items-center gap-2 text-2xl font-semibold leading-snug text-fg-default">
            <BookOpen aria-hidden size={22} strokeWidth={1.75} className="text-accent-default" />
            <span>내 지식</span>
          </h1>
          <p className="text-sm text-fg-muted">
            내 개인 KB 를 관리합니다 — 내가 owner 인 KB 만 보이고,
            업로드한 문서는 다른 사람에게 노출되지 않습니다.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-fg-muted">
          <span>
            {kbs.length} / {PERSONAL_KB_LIMIT}
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setCatalogOpen(true)}
            disabled={!selected}
            title={
              !selected
                ? "먼저 KB 를 선택하거나 만드세요"
                : "데이터 가져오기 (파일 업로드 / 외부 connector)"
            }
          >
            <Download aria-hidden size={14} strokeWidth={1.75} className="mr-1 inline-block" />
            데이터 가져오기
          </Button>
          <Button
            size="sm"
            disabled={atCap}
            onClick={() => setCreating(true)}
            title={atCap ? `최대 ${PERSONAL_KB_LIMIT}개까지 생성 가능` : undefined}
          >
            + 새 KB 만들기
          </Button>
        </div>
      </header>

      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, idx) => (
            <Skeleton key={idx} className="h-32" />
          ))}
        </div>
      ) : isError ? (
        <ErrorFallback
          title="내 KB 목록을 불러올 수 없습니다"
          error={error}
          onRetry={() => refetch()}
        />
      ) : kbs.length === 0 ? (
        <EmptyState
          icon={<BookOpen size={32} strokeWidth={1.5} />}
          title="아직 personal KB 가 없습니다"
          description="첫 KB 를 만들고 문서를 업로드해 보세요. 가입 시 자동으로 생성된 기본 KB 가 보이지 않는다면 새로 만들어 주세요."
          action={<Button onClick={() => setCreating(true)}>+ 새 KB 만들기</Button>}
        />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
          <aside className="space-y-3">
            {kbs.map((kb) => (
              <KbCard
                key={kb.kb_id}
                kb={kb}
                userId={userId}
                selected={selected?.kb_id === kb.kb_id}
                onSelect={() => setSelectedKbId(kb.kb_id)}
              />
            ))}
          </aside>

          <div className="space-y-4">
            {selected ? (
              <>
                <header className="space-y-1">
                  <h2 className="text-lg font-medium text-fg-default">
                    {selected.name}
                  </h2>
                  <p className="text-xs text-fg-muted">
                    문서 {selected.document_count ?? 0}개 · chunk{" "}
                    {selected.chunk_count ?? 0}개
                  </p>
                </header>
                <div id="personal-uploader" tabIndex={-1}>
                  <DocumentUploader kbId={selected.kb_id} />
                </div>
                <DocumentList
                  documents={docs.data?.documents ?? []}
                  total={docs.data?.total ?? 0}
                  isLoading={docs.isLoading}
                  isError={docs.isError}
                  errorMessage={
                    docs.error instanceof Error ? docs.error.message : undefined
                  }
                />
              </>
            ) : (
              <EmptyState
                icon={<FolderOpen size={32} strokeWidth={1.5} />}
                title="KB 를 선택하세요"
                description="좌측에서 KB 를 클릭하면 문서 업로드 영역이 보입니다."
              />
            )}
          </div>
        </div>
      )}

      {creating && (
        <CreateKbDialog
          userId={userId}
          onClose={() => setCreating(false)}
        />
      )}

      <ConnectorCatalog
        open={catalogOpen}
        onClose={() => setCatalogOpen(false)}
        onSelect={onPickConnector}
        scope="user"
        title="데이터 가져오기"
        description="파일 업로드 또는 외부 소스에서 KB 로 가져옵니다. 회색 카드는 곧 출시 예정입니다."
      />

      {pickedConnector && selected && (
        <UserDataSourceDialog
          open={true}
          kbId={selected.kb_id}
          connector={pickedConnector}
          onClose={() => setPickedConnector(null)}
          onCreated={() => {
            // 등록 후 KB 카드의 source 카운트 등 갱신될 수 있도록.
            refetch();
          }}
        />
      )}
    </section>
  );
}
