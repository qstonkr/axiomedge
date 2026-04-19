"use client";

import { useState, type FormEvent } from "react";

import {
  Badge,
  Button,
  Dialog,
  Input,
  Select,
  Skeleton,
  useToast,
} from "@/components/ui";
import {
  useAssignAuthRole,
  useAuthRoles,
  useAuthUsers,
  useCreateAuthUser,
  useDeleteAuthUser,
  useUpdateAuthUser,
} from "@/hooks/admin/useOps";
import type { AuthUser, AuthUserUpsertBody } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

export function UsersClient() {
  const toast = useToast();
  const users = useAuthUsers();
  const roles = useAuthRoles();
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<AuthUser | null>(null);
  const [creating, setCreating] = useState(false);
  const [roleAssignTarget, setRoleAssignTarget] = useState<AuthUser | null>(null);

  const create = useCreateAuthUser();
  const update = useUpdateAuthUser();
  const del = useDeleteAuthUser();
  const assign = useAssignAuthRole();

  async function onDelete(u: AuthUser) {
    if (!confirm(`'${u.email}' 사용자를 삭제하시겠습니까?\n복구 불가합니다.`))
      return;
    try {
      await del.mutateAsync(u.id);
      toast.push("삭제되었습니다", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    }
  }

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
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (u) => (
        <div className="flex justify-end gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setRoleAssignTarget(u)}
          >
            권한
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(u)}>
            수정
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onDelete(u)}>
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
          <h1 className="text-xl font-semibold text-fg-default">사용자/권한</h1>
          <p className="text-sm text-fg-muted">
            시스템 등록 사용자 + provider + 활성 상태 + 시스템 role 할당.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          + 신규 사용자
        </Button>
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

      <UserFormDialog
        key={editing?.id ?? (creating ? "create" : "closed")}
        open={creating || editing !== null}
        initial={editing}
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
              toast.push("신규 사용자가 추가되었습니다", "success");
            }
            setCreating(false);
            setEditing(null);
          } catch (e) {
            toast.push(e instanceof Error ? e.message : "저장 실패", "danger");
          }
        }}
        pending={create.isPending || update.isPending}
      />

      <RoleAssignDialog
        open={roleAssignTarget !== null}
        user={roleAssignTarget}
        roles={roles.data ?? []}
        onClose={() => setRoleAssignTarget(null)}
        onAssign={async (role) => {
          if (!roleAssignTarget) return;
          try {
            await assign.mutateAsync({ userId: roleAssignTarget.id, role });
            toast.push(`'${role}' 권한 부여됨`, "success");
            setRoleAssignTarget(null);
          } catch (e) {
            toast.push(e instanceof Error ? e.message : "권한 부여 실패", "danger");
          }
        }}
      />
    </section>
  );
}

function UserFormDialog({
  open,
  initial,
  onClose,
  onSubmit,
  pending,
}: {
  open: boolean;
  initial: AuthUser | null;
  onClose: () => void;
  onSubmit: (body: AuthUserUpsertBody) => void;
  pending: boolean;
}) {
  const [email, setEmail] = useState(initial?.email ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [department, setDepartment] = useState(initial?.department ?? "");
  const [password, setPassword] = useState("");
  const [isActive, setIsActive] = useState(initial?.is_active !== false);

  function submit(e: FormEvent) {
    e.preventDefault();
    const body: AuthUserUpsertBody = {
      email: email.trim(),
      display_name: displayName.trim() || null,
      department: department.trim() || null,
      is_active: isActive,
    };
    if (password) body.password = password;
    onSubmit(body);
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={initial ? `사용자 수정 — ${initial.email}` : "신규 사용자"}
      width="md"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="user-form" disabled={pending || !email.trim()}>
            {pending ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <form id="user-form" onSubmit={submit} className="space-y-3">
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          이메일
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
            disabled={Boolean(initial)}
          />
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            표시 이름
            <Input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            부서
            <Input
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
            />
          </label>
        </div>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          비밀번호
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={initial ? "(변경 없음)" : "신규 사용자 비밀번호"}
            required={!initial}
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
          />
          <span className="text-fg-default">활성</span>
        </label>
      </form>
    </Dialog>
  );
}

function RoleAssignDialog({
  open,
  user,
  roles,
  onClose,
  onAssign,
}: {
  open: boolean;
  user: AuthUser | null;
  roles: { name: string; display_name?: string | null }[];
  onClose: () => void;
  onAssign: (role: string) => void;
}) {
  const [selected, setSelected] = useState(roles[0]?.name ?? "");
  if (!user) return null;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`권한 부여 — ${user.email}`}
      description="시스템 role 부여 (취소는 별도 메뉴 — 추후)"
      width="sm"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button onClick={() => selected && onAssign(selected)} disabled={!selected}>
            부여
          </Button>
        </>
      }
    >
      <label className="block space-y-1 text-xs font-medium text-fg-muted">
        Role
        <Select value={selected} onChange={(e) => setSelected(e.target.value)}>
          <option value="">— role 선택 —</option>
          {roles.map((r) => (
            <option key={r.name} value={r.name}>
              {r.name}
              {r.display_name ? ` (${r.display_name})` : ""}
            </option>
          ))}
        </Select>
      </label>
    </Dialog>
  );
}
