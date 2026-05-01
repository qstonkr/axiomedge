"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect } from "react";
import { Bell } from "lucide-react";

import { OrgSwitcher } from "@/components/layout/OrgSwitcher";
import { useAdminDashboardSummary } from "@/hooks/admin/useAdminDashboard";
import type { Membership } from "@/lib/auth/session";

import { AdminMobileNav } from "./AdminMobileNav";
import { AdminQuickPalette } from "./AdminQuickPalette";

/**
 * Admin sticky header — breadcrumb + 컨텍스트 액션 + OrgSwitcher.
 *
 * `<html data-admin="true">` 마킹은 여기서 mount/unmount lifecycle 에 맞춰
 * 처리한다 — 사용자가 admin 영역 떠나면 토큰 override 도 같이 풀린다.
 */
const ROUTE_LABELS: Record<string, string> = {
  admin: "운영 대시보드",
  sources: "데이터 소스",
  ingest: "Ingest 작업",
  glossary: "용어집",
  owners: "담당자 관리",
  groups: "검색 그룹",
  conflicts: "중복/모순",
  verification: "검증 대기",
  lifecycle: "문서 라이프사이클",
  quality: "RAG 품질",
  "golden-set": "Golden Set",
  traces: "Agent Trace",
  errors: "오류 신고",
  users: "사용자/권한",
  edge: "Edge 모델",
  jobs: "작업 모니터",
  config: "가중치 설정",
  graph: "엔티티 탐색",
  "graph-schema": "스키마 검토",
};

export function AdminHeader({
  activeOrgId,
  memberships,
  displayName,
  actions,
}: {
  activeOrgId: string | null;
  memberships: Membership[];
  displayName: string;
  actions?: ReactNode;
}) {
  const pathname = usePathname();

  // mount 시 admin 토큰 활성, unmount 시 해제 — 사용자 영역과 시각 분리 보장
  useEffect(() => {
    document.documentElement.dataset.admin = "true";
    return () => {
      delete document.documentElement.dataset.admin;
    };
  }, []);

  // breadcrumb: ['admin', 'sources'] → "운영 대시보드 / 데이터 소스"
  const segments = pathname.split("/").filter(Boolean);
  const crumbs: { href: string; label: string }[] = [];
  let acc = "";
  for (const seg of segments) {
    acc += `/${seg}`;
    crumbs.push({ href: acc, label: ROUTE_LABELS[seg] ?? seg });
  }

  return (
    <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border-default bg-bg-canvas px-3 md:px-6">
      <div className="flex min-w-0 items-center gap-2">
        <AdminMobileNav />
        <nav aria-label="현재 위치" className="flex items-center gap-2 text-xs">
          {crumbs.map((c, i) => (
            <span key={c.href} className="flex items-center gap-2">
              {i > 0 && <span className="text-fg-subtle" aria-hidden>›</span>}
              {i === crumbs.length - 1 ? (
                <span className="font-medium text-fg-default">{c.label}</span>
              ) : (
                <Link
                  href={c.href}
                  className="text-fg-muted underline-offset-4 hover:text-accent-default hover:underline focus-visible:text-accent-default focus-visible:underline"
                >
                  {c.label}
                </Link>
              )}
            </span>
          ))}
        </nav>
      </div>
      <div className="flex items-center gap-2">
        {actions}
        <AdminQuickPalette />
        <NotificationBell />
        <OrgSwitcher
          activeOrgId={activeOrgId}
          memberships={memberships}
          displayName={displayName}
        />
      </div>
    </header>
  );
}

function NotificationBell() {
  // 대시보드 summary 의 pending counter 를 합쳐 alert badge — 새 endpoint 없이
  // 기존 데이터 재사용. 30s polling 도 그대로.
  const { data } = useAdminDashboardSummary();
  const pending =
    (data?.feedback_pending ?? 0) + (data?.error_reports_pending ?? 0);
  return (
    <Link
      href={pending > 0 ? "/admin/errors" : "/admin"}
      title={`대기 ${pending}건 (피드백 + 오류 신고)`}
      aria-label={`알림 ${pending}건`}
      className="relative flex h-7 w-7 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-muted hover:text-fg-default"
    >
      <Bell aria-hidden size={16} strokeWidth={1.75} />
      {pending > 0 && (
        <span
          aria-hidden
          className="absolute right-0 top-0 flex h-3.5 min-w-[14px] items-center justify-center rounded-full border border-bg-canvas bg-danger-default px-0.5 font-mono text-[9px] font-medium text-fg-onAccent"
        >
          {pending > 99 ? "99+" : pending}
        </span>
      )}
    </Link>
  );
}
