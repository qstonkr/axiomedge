/**
 * Admin nav SSOT — Sidebar / QuickPalette / MobileNav 가 모두 사용.
 *
 * 이전엔 AdminSidebar.tsx 와 AdminQuickPalette.tsx 가 각자 emoji icon 으로
 * 19 nav item 을 hardcode 해서 두 곳에서 drift. 단일 source 로 통합 +
 * Lucide 아이콘 (DESIGN.md 톤 정합).
 */
import {
  AlertTriangle,
  BookOpen,
  Boxes,
  FileSearch,
  FlaskConical,
  Folders,
  Gauge,
  Globe,
  ListChecks,
  Medal,
  Network,
  Plug,
  Recycle,
  Route,
  Settings,
  Shield,
  ShieldAlert,
  Sliders,
  TrendingUp,
  Users,
  type LucideIcon,
} from "lucide-react";

export type AdminNavItem = {
  href: string;
  label: string;
  group: AdminNavGroupLabel;
  Icon: LucideIcon;
};

export type AdminNavGroupLabel =
  | "개요"
  | "콘텐츠 관리"
  | "품질·평가"
  | "시스템 운영"
  | "그래프";

export const ADMIN_NAV: AdminNavItem[] = [
  { href: "/admin", label: "운영 대시보드", group: "개요", Icon: Gauge },

  { href: "/admin/sources", label: "데이터 소스", group: "콘텐츠 관리", Icon: Plug },
  { href: "/admin/ingest", label: "Ingest 작업", group: "콘텐츠 관리", Icon: Settings },
  { href: "/admin/glossary", label: "용어집", group: "콘텐츠 관리", Icon: BookOpen },
  { href: "/admin/owners", label: "담당자 관리", group: "콘텐츠 관리", Icon: Users },
  { href: "/admin/groups", label: "검색 그룹", group: "콘텐츠 관리", Icon: Folders },
  { href: "/admin/conflicts", label: "중복/모순", group: "콘텐츠 관리", Icon: AlertTriangle },
  { href: "/admin/verification", label: "검증 대기", group: "콘텐츠 관리", Icon: FileSearch },
  { href: "/admin/lifecycle", label: "문서 라이프사이클", group: "콘텐츠 관리", Icon: Recycle },

  { href: "/admin/quality", label: "RAG 품질", group: "품질·평가", Icon: TrendingUp },
  { href: "/admin/golden-set", label: "Golden Set", group: "품질·평가", Icon: Medal },
  { href: "/admin/traces", label: "Agent Trace", group: "품질·평가", Icon: Route },
  { href: "/admin/errors", label: "오류 신고", group: "품질·평가", Icon: ShieldAlert },

  { href: "/admin/users", label: "사용자/권한", group: "시스템 운영", Icon: Shield },
  { href: "/admin/edge", label: "Edge 모델", group: "시스템 운영", Icon: Globe },
  { href: "/admin/jobs", label: "작업 모니터", group: "시스템 운영", Icon: ListChecks },
  { href: "/admin/config", label: "가중치 설정", group: "시스템 운영", Icon: Sliders },

  { href: "/admin/graph", label: "엔티티 탐색", group: "그래프", Icon: Network },
  { href: "/admin/graph-schema", label: "스키마 검토", group: "그래프", Icon: FlaskConical },
];

/** 그룹별로 묶어 sidebar 가 사용하는 형태로 변환. */
export function groupAdminNav(): { label: AdminNavGroupLabel; items: AdminNavItem[] }[] {
  const order: AdminNavGroupLabel[] = [
    "개요",
    "콘텐츠 관리",
    "품질·평가",
    "시스템 운영",
    "그래프",
  ];
  return order.map((label) => ({
    label,
    items: ADMIN_NAV.filter((item) => item.group === label),
  }));
}

// Re-export for convenience
export { Boxes };
