"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { OrgSwitcher } from "@/components/layout/OrgSwitcher";
import type { Membership } from "@/lib/auth/session";

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
  quality: "RAG 품질",
  "golden-set": "Golden Set",
  traces: "Agent Trace",
  errors: "오류 신고",
  users: "사용자/권한",
  edge: "Edge 모델",
  jobs: "작업 모니터",
  config: "가중치 설정",
  graph: "엔티티 탐색",
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
    <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center justify-between border-b border-border-default bg-bg-canvas px-6 backdrop-blur">
      <nav aria-label="현재 위치" className="flex items-center gap-2 text-xs">
        {crumbs.map((c, i) => (
          <span key={c.href} className="flex items-center gap-2">
            {i > 0 && <span className="text-fg-subtle" aria-hidden>›</span>}
            {i === crumbs.length - 1 ? (
              <span className="font-medium text-fg-default">{c.label}</span>
            ) : (
              <Link
                href={c.href}
                className="text-fg-muted hover:text-fg-default"
              >
                {c.label}
              </Link>
            )}
          </span>
        ))}
      </nav>
      <div className="flex items-center gap-3">
        {actions}
        <OrgSwitcher
          activeOrgId={activeOrgId}
          memberships={memberships}
          displayName={displayName}
        />
      </div>
    </header>
  );
}
