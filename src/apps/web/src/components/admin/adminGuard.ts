import "server-only";

import type { Session } from "@/lib/auth/session";

/**
 * Admin role 식별 — OWNER 또는 ADMIN 으로 선언된 role 이 하나라도 있으면 true.
 *
 * scope_type 은 따지지 않는다 (org-scope ADMIN 도 admin 페이지 접근 가능).
 * 더 세밀한 권한 (특정 KB 만 / 특정 그룹만) 은 페이지 단에서 추가 체크.
 *
 * roles 배열은 ``[{ role: "ADMIN", scope_type: "organization", scope_id: "..." }, ...]``
 * 형태이고 backend ``/auth/me`` 가 채워준다.
 */
const ADMIN_ROLES: ReadonlySet<string> = new Set(["OWNER", "ADMIN"]);

export function hasAdminRole(session: Session | null): boolean {
  if (!session) return false;
  for (const r of session.roles ?? []) {
    if (ADMIN_ROLES.has(r.role?.toUpperCase?.() ?? "")) return true;
  }
  return false;
}
