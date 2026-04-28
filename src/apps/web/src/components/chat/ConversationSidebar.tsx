"use client";

import { useMemo, useState } from "react";

import { ProfileDropdown } from "@/components/layout/ProfileDropdown";
import type { Conversation } from "@/lib/api/chat";
import { useConversations, useCreateConversation } from "@/store/conversations";

import { ConversationItem } from "./ConversationItem";

type Bucket = { label: string; items: Conversation[] };

function bucketize(items: Conversation[]): Bucket[] {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);
  const buckets: Bucket[] = [
    { label: "오늘", items: [] },
    { label: "어제", items: [] },
    { label: "이번 주", items: [] },
    { label: "이전", items: [] },
  ];
  for (const c of items) {
    const t = new Date(c.updated_at);
    if (t >= today) buckets[0].items.push(c);
    else if (t >= yesterday) buckets[1].items.push(c);
    else if (t >= weekAgo) buckets[2].items.push(c);
    else buckets[3].items.push(c);
  }
  return buckets.filter((b) => b.items.length > 0);
}

export function ConversationSidebar({
  activeId,
  onSelect,
  userEmail,
}: {
  activeId: string | null;
  onSelect?: (id: string) => void;
  userEmail?: string;
}) {
  const { data = [], isLoading } = useConversations();
  const create = useCreateConversation();
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const norm = q.trim().toLowerCase();
    if (!norm) return data;
    return data.filter((c) => c.title.toLowerCase().includes(norm));
  }, [data, q]);

  const buckets = useMemo(() => bucketize(filtered), [filtered]);

  async function newChat() {
    const id = await create.mutateAsync({ kb_ids: [] });
    onSelect?.(id);
  }

  return (
    <aside className="hidden w-64 shrink-0 self-stretch border-r border-border-default bg-bg-subtle px-3 py-3 md:flex md:flex-col">
      <button
        onClick={newChat}
        className="mb-3 rounded-md border border-border-default px-3 py-2 text-sm hover:bg-bg-muted"
      >
        + 새 대화
      </button>
      <input
        type="search"
        placeholder="대화 검색"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        className="mb-3 rounded-md border border-border-default bg-bg-default px-2 py-1 text-sm"
      />
      <div className="flex-1 overflow-y-auto pr-1">
        {isLoading && <p className="text-xs text-fg-muted">불러오는 중…</p>}
        {!isLoading && data.length === 0 && (
          <p className="text-xs text-fg-muted">대화 기록이 없습니다.</p>
        )}
        {buckets.map((b) => (
          <div key={b.label} className="mb-3">
            <p className="mb-1 px-2 text-xs uppercase text-fg-subtle">{b.label}</p>
            {b.items.map((c) => (
              <ConversationItem
                key={c.id}
                id={c.id}
                title={c.title}
                active={c.id === activeId}
                onSelect={(id) => onSelect?.(id)}
              />
            ))}
          </div>
        ))}
      </div>
      {userEmail && (
        <div className="mt-2 border-t border-border-default pt-2">
          <ProfileDropdown email={userEmail} />
        </div>
      )}
    </aside>
  );
}
