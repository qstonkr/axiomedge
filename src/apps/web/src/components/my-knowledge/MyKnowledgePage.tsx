"use client";

import { useState } from "react";

import {
  Button,
  EmptyState,
  Skeleton,
} from "@/components/ui";
import { useMyPersonalKbs } from "@/hooks/useMyKnowledge";

import { CreateKbDialog } from "./CreateKbDialog";
import { DocumentUploader } from "./DocumentUploader";
import { KbCard } from "./KbCard";

const PERSONAL_KB_LIMIT = 10;

export function MyKnowledgePage({ userId }: { userId: string }) {
  const { data, isLoading } = useMyPersonalKbs(userId);
  const [creating, setCreating] = useState(false);
  const [selectedKbId, setSelectedKbId] = useState<string | null>(null);

  const kbs = data ?? [];
  const selected = kbs.find((kb) => kb.kb_id === selectedKbId) ?? kbs[0];
  const atCap = kbs.length >= PERSONAL_KB_LIMIT;

  return (
    <section className="mx-auto w-full max-w-5xl space-y-6 px-6 py-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold leading-snug text-fg-default">
            📚 내 지식
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
      ) : kbs.length === 0 ? (
        <EmptyState
          icon="📚"
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
                <DocumentUploader kbId={selected.kb_id} />
                <p className="text-xs text-fg-subtle">
                  업로드된 문서 목록은 곧 표시됩니다 (Day 7 의 문서 인덱스
                  파이프라인과 연결 필요 — 후속 작업).
                </p>
              </>
            ) : (
              <EmptyState
                icon="📂"
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
    </section>
  );
}
