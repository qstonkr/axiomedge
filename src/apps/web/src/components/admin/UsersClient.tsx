"use client";

import { useState, type FormEvent } from "react";

import {
  Badge,
  Button,
  Dialog,
  ErrorFallback,
  Input,
  Select,
  Skeleton,
  Tabs,
  useToast,
} from "@/components/ui";
import {
  useAbacPolicies,
  useAssignAuthRole,
  useAuthRoles,
  useAuthUsers,
  useCreateAuthUser,
  useDeleteAuthUser,
  useKbPermissions,
  useUpdateAuthUser,
} from "@/hooks/admin/useOps";
import { useSearchableKbs } from "@/hooks/useSearch";
import type {
  AbacPolicy,
  AuthUser,
  AuthUserUpsertBody,
  KbPermission,
} from "@/lib/api/endpoints";

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

  const usersTab = (
    <div className="space-y-4">
      <div className="flex items-end justify-end">
        <Button size="sm" onClick={() => setCreating(true)}>
          + 신규 사용자
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <MetricCard label="총 사용자" value={items.length} />
        <MetricCard
          label="활성"
          value={items.filter((u) => u.is_active !== false).length}
          tone="success"
        />
        <MetricCard label="시스템 role" value={(roles.data ?? []).length} />
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
        <ErrorFallback
          title="사용자 목록을 불러올 수 없습니다"
          error={users.error}
          onRetry={() => users.refetch()}
        />
      ) : (
        <DataTable<AuthUser>
          columns={columns}
          rows={filtered}
          rowKey={(r) => r.id}
          empty={filter ? "검색 결과 없음" : "등록된 사용자가 없습니다"}
        />
      )}
    </div>
  );

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">사용자/권한</h1>
        <p className="text-sm text-fg-muted">
          시스템 사용자 / KB 별 권한 / ABAC 정책. 사용자 탭에서 신규 추가
          및 시스템 role 할당.
        </p>
      </header>

      <Tabs
        items={[
          { id: "users", label: "사용자 관리", content: usersTab },
          {
            id: "kb_perms",
            label: "KB 권한",
            content: <KbPermissionsTab />,
          },
          {
            id: "abac",
            label: "ABAC 정책",
            content: <AbacPoliciesTab />,
          },
        ]}
      />

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

/**
 * KB 권한 탭 — KB 선택 후 그 KB 의 사용자 권한 (reader/contributor/manager/
 * owner) 목록 표시. 변경 (set/revoke) 은 후속 (현재는 read-only viewer).
 */
function KbPermissionsTab() {
  const kbs = useSearchableKbs();
  const [kbId, setKbId] = useState("");
  const perms = useKbPermissions(kbId || null);

  const columns: Column<KbPermission>[] = [
    {
      key: "user_id",
      header: "사용자",
      render: (p) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg-default">
            {p.email ?? p.user_id}
          </span>
          {p.display_name && (
            <span className="text-[10px] text-fg-subtle">{p.display_name}</span>
          )}
        </div>
      ),
    },
    {
      key: "permission_level",
      header: "권한",
      render: (p) => {
        const tone =
          p.permission_level === "owner"
            ? "danger"
            : p.permission_level === "manager"
              ? "warning"
              : p.permission_level === "contributor"
                ? "accent"
                : "neutral";
        return <Badge tone={tone}>{p.permission_level}</Badge>;
      },
    },
    {
      key: "granted_by",
      header: "부여자",
      render: (p) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {p.granted_by ?? "—"}
        </span>
      ),
    },
    {
      key: "granted_at",
      header: "부여 시각",
      render: (p) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {(p.granted_at ?? "").slice(0, 19).replace("T", " ")}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border-default bg-bg-canvas p-4">
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          KB 선택
          <Select value={kbId} onChange={(e) => setKbId(e.target.value)}>
            <option value="">— KB 선택 —</option>
            {(kbs.data ?? []).map((kb) => (
              <option key={kb.kb_id} value={kb.kb_id}>
                {kb.name} ({kb.kb_id})
              </option>
            ))}
          </Select>
        </label>
      </div>
      {!kbId ? (
        <div className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-8 text-center text-xs text-fg-muted">
          KB 를 선택하면 그 KB 의 사용자 권한이 표시됩니다.
        </div>
      ) : perms.isLoading ? (
        <Skeleton className="h-32" />
      ) : perms.isError ? (
        <ErrorFallback
          title="권한 목록을 불러올 수 없습니다"
          error={perms.error}
          onRetry={() => perms.refetch()}
        />
      ) : (
        <DataTable<KbPermission>
          columns={columns}
          rows={perms.data?.permissions ?? []}
          rowKey={(r) => r.user_id}
          empty="이 KB 의 명시적 권한 부여가 없습니다 (조직 role 만 적용)."
        />
      )}
      <p className="text-xs text-fg-subtle">
        💡 권한 변경 (set/revoke) 은 backend
        <code className="font-mono">POST /auth/kb/{`{kb_id}`}/permissions</code>{" "}
        로 가능 — UI 는 후속 단계.
      </p>
    </div>
  );
}

/**
 * ABAC 정책 탭 — 정책 list (read-only viewer). 조건 평가 자체는 backend
 * 가, UI 는 정책 우선순위/effect/조건을 한눈에. 신규 정책 작성 form 은
 * 후속.
 */
function AbacPoliciesTab() {
  const policies = useAbacPolicies();

  const columns: Column<AbacPolicy>[] = [
    {
      key: "name",
      header: "정책",
      render: (p) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg-default">{p.name}</span>
          {p.description && (
            <span className="text-[10px] text-fg-subtle">
              {p.description}
            </span>
          )}
        </div>
      ),
    },
    {
      key: "effect",
      header: "효과",
      render: (p) => (
        <Badge tone={p.effect === "allow" ? "success" : "danger"}>
          {p.effect}
        </Badge>
      ),
    },
    {
      key: "resource_type",
      header: "리소스",
      render: (p) => (
        <span className="font-mono text-[10px] text-fg-default">
          {p.resource_type}
        </span>
      ),
    },
    {
      key: "action",
      header: "액션",
      render: (p) => (
        <span className="font-mono text-[10px] text-fg-default">{p.action}</span>
      ),
    },
    {
      key: "priority",
      header: "우선순위",
      align: "right",
      render: (p) => (
        <span className="font-mono tabular-nums text-fg-default">
          {p.priority}
        </span>
      ),
    },
    {
      key: "is_active",
      header: "상태",
      render: (p) => (
        <Badge tone={p.is_active ? "success" : "neutral"}>
          {p.is_active ? "활성" : "비활성"}
        </Badge>
      ),
    },
    {
      key: "conditions",
      header: "조건",
      render: (p) => {
        const c =
          typeof p.conditions === "string"
            ? p.conditions
            : JSON.stringify(p.conditions);
        return (
          <code
            className="line-clamp-2 break-all font-mono text-[10px] text-fg-muted"
            title={c}
          >
            {c}
          </code>
        );
      },
    },
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <MetricCard label="총 정책" value={(policies.data ?? []).length} />
        <MetricCard
          label="allow"
          value={(policies.data ?? []).filter((p) => p.effect === "allow").length}
          tone="success"
        />
        <MetricCard
          label="deny"
          value={(policies.data ?? []).filter((p) => p.effect === "deny").length}
          tone="danger"
        />
      </div>
      {policies.isLoading ? (
        <Skeleton className="h-32" />
      ) : policies.isError ? (
        <ErrorFallback
          title="ABAC 정책을 불러올 수 없습니다"
          error={policies.error}
          onRetry={() => policies.refetch()}
        />
      ) : (
        <DataTable<AbacPolicy>
          columns={columns}
          rows={policies.data ?? []}
          rowKey={(r) => r.id}
          empty="등록된 ABAC 정책이 없습니다."
        />
      )}
      <p className="text-xs text-fg-subtle">
        💡 정책 작성 / 편집은 backend
        <code className="font-mono">POST/PUT /auth/abac/policies</code> 로
        가능 — UI 는 후속 단계.
      </p>
    </div>
  );
}
