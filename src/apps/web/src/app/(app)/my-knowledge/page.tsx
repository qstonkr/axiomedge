"use client";

import { useEffect, useState } from "react";

type KbRow = { kb_id: string; name: string; favorite?: boolean };

export default function MyKnowledgePage() {
  const [kbs, setKbs] = useState<KbRow[]>([]);
  useEffect(() => {
    fetch("/api/proxy/api/v1/admin/kb?status=active")
      .then((r) => r.json())
      .then((d: { kbs: KbRow[] }) => setKbs(d.kbs ?? []));
  }, []);
  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="text-xl font-semibold">내 KB 관리</h1>
      <p className="mt-2 text-sm text-fg-muted">
        조직에 활성화된 KB 목록입니다. 채팅 화면 상단의 KB chip 에서 검색
        대상 KB 를 직접 선택할 수 있어요.
      </p>
      <p className="mt-1 text-xs text-fg-subtle">
        즐겨찾기·기본 KB 설정은 준비 중 — 다음 릴리스에서 활성화됩니다.
      </p>
      <ul className="mt-6 space-y-2">
        {kbs.map((kb) => (
          <li
            key={kb.kb_id}
            className="rounded-md border border-border-default px-3 py-2"
          >
            <span className="text-sm">
              {kb.name}{" "}
              <span className="text-xs text-fg-muted">{kb.kb_id}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
