"use client";

import { useState } from "react";

import { Badge, Input, Skeleton } from "@/components/ui";
import { useAuthRoles, useAuthUsers } from "@/hooks/admin/useOps";
import type { AuthUser } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

export function UsersClient() {
  const users = useAuthUsers();
  const roles = useAuthRoles();
  const [filter, setFilter] = useState("");

  const items = users.data?.users ?? [];
  const filtered = filter
    ? items.filter((u) =>
        ((u.email ?? "") + " " + (u.display_name ?? ""))
          .toLowerCase()
          .includes(filter.toLowerCase()),
      )
    : items;

  const columns: Column<AuthUser>[] = [
    {
      key: "email",
      header: "이메일",
      render: (u) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg-default">{u.email}</span>
          <span className="text-[10px] text-fg-subtle">{u.display_name ?? "—"}</span>
        </div>
      ),
    },
    {
      key: "provider",
      header: "Provider",
      render: (u) => (
        <Badge tone="neutral">{u.provider ?? "internal"}</Badge>
      ),
    },
    {
      key: "department",
      header: "부서",
      render: (u) => (
        <span className="text-fg-muted">{u.department ?? "—"}</span>
      ),
    },
    {
      key: "is_active",
      header: "상태",
      render: (u) =>
        u.is_active === false ? (
          <Badge tone="danger">비활성</Badge>
        ) : (
          <Badge tone="success">활성</Badge>
        ),
    },
    {
      key: "id",
      header: "ID",
      render: (u) => (
        <span className="font-mono text-[10px] text-fg-subtle">
          {u.id.slice(0, 8)}…
        </span>
      ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">사용자/권한</h1>
        <p className="text-sm text-fg-muted">
          시스템 등록 사용자 목록 + provider + 활성 상태. 권한 변경은 추후 (PR)
          별도 페이지에서.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <MetricCard label="총 사용자" value={items.length} />
        <MetricCard
          label="활성"
          value={items.filter((u) => u.is_active !== false).length}
          tone="success"
        />
        <MetricCard
          label="시스템 role"
          value={(roles.data ?? []).length}
        />
        <div className="rounded-lg border border-border-default bg-bg-canvas p-4">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            검색
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="이메일/이름 부분 일치"
            />
          </label>
        </div>
      </div>

      {users.isLoading ? (
        <Skeleton className="h-48" />
      ) : users.isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm text-danger-default">
          사용자 목록을 불러올 수 없습니다
        </div>
      ) : (
        <DataTable<AuthUser>
          columns={columns}
          rows={filtered}
          rowKey={(r) => r.id}
          empty={filter ? "검색 결과 없음" : "등록된 사용자가 없습니다"}
        />
      )}
    </section>
  );
}
