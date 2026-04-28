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
        검색 시 기본으로 사용할 KB를 즐겨찾기로 표시합니다. 즐겨찾기는
        채팅의 KB chip 에 우선 노출됩니다.
      </p>
      <ul className="mt-6 space-y-2">
        {kbs.map((kb) => (
          <li
            key={kb.kb_id}
            className="flex items-center justify-between rounded-md border border-border-default px-3 py-2"
          >
            <span className="text-sm">
              {kb.name}{" "}
              <span className="text-xs text-fg-muted">{kb.kb_id}</span>
            </span>
            <button className="text-xs">⭐ 즐겨찾기</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
