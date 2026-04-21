/**
 * Connector catalog SSOT — admin/사용자 화면이 같은 metadata 로 카드 grid 렌더링.
 *
 * 추가 절차:
 * 1. 새 connector 백엔드 구현 (src/connectors/<name>/) → source_type 결정
 * 2. 본 파일 ``CONNECTOR_CATALOG`` 에 entry 추가 (status/scope 포함)
 * 3. 그게 끝 — 카드는 자동으로 Admin/User 화면에 노출됨
 *
 * status 정책:
 *  - ``available`` — 백엔드 구현 완료 + 프로덕션 사용 가능
 *  - ``preview``   — 구현 됐으나 검증 중 (admin 일부에게만 노출 가능)
 *  - ``planned``   — 로드맵 — 카드는 보이지만 클릭 비활성 (사용자에게 "곧 출시" 시각화)
 */

export type ConnectorStatus = "available" | "preview" | "planned";

export type ConnectorScope = "user" | "admin" | "both";

export type ConnectorCategory =
  | "files"
  | "wiki"
  | "code"
  | "office"
  | "chat"
  | "issue"
  | "crawl";

export type UserTokenMode = "per-user" | "shared" | "none";

export type ConnectorEntry = {
  /** 백엔드 ``source_type`` 키 — POST /data-sources 의 source_type 으로 그대로 전송. */
  id: string;
  label: string;
  category: ConnectorCategory;
  /** Emoji or single-grapheme — UI 우상단 큰 아이콘. */
  icon: string;
  description: string;
  status: ConnectorStatus;
  scope: ConnectorScope;
  /**
   * 토큰 입력 모드 — 사용자 화면 form 동작 결정.
   *  - ``per-user``: 사용자 본인 PAT 입력 (Notion/Git/Confluence)
   *  - ``shared``: admin 이 등록한 organization-wide bot token 사용 (Slack)
   *    — form 에서 token input hidden, "관리자가 등록한 bot 사용" 안내
   *  - ``none``: 토큰 불필요 (file_upload, crawl_result)
   * 백엔드 ``src/connectors/catalog_meta.py`` 와 sync 유지.
   */
  userTokenMode: UserTokenMode;
  /**
   * crawl_config JSON 입력 example placeholder.
   * available connector 에 권장 — 사용자가 schema 추측 안 해도 됨.
   */
  configSchema?: string;
  /** ``planned`` 상태일 때 표시할 외부 문서/이슈 링크 (옵션). */
  docsUrl?: string;
};

export const CONNECTOR_CATALOG: readonly ConnectorEntry[] = [
  // ===== 파일 / 객체 스토리지 =====
  {
    id: "file_upload",
    label: "파일 업로드",
    category: "files",
    icon: "📄",
    description: "PDF / DOCX / PPTX / MD / TXT 파일 직접 업로드",
    status: "available",
    scope: "both",
    userTokenMode: "none",
  },
  {
    id: "crawl_result",
    label: "크롤 결과 임포트",
    category: "files",
    icon: "🗂️",
    description: "기존 크롤 결과 (JSONL) 파일 직접 임포트",
    status: "available",
    scope: "admin",
    userTokenMode: "none",
  },

  // ===== Wiki / Documentation =====
  {
    id: "confluence",
    label: "Confluence",
    category: "wiki",
    icon: "📘",
    description: "Atlassian Confluence 페이지 트리 BFS 크롤링 (본인 PAT 사용)",
    status: "available",
    scope: "both",
    userTokenMode: "per-user",
    configSchema: JSON.stringify(
      {
        base_url: "https://wiki.example.com",
        page_id: "1234567",
        max_depth: 5,
      },
      null,
      2,
    ),
  },
  {
    id: "notion",
    label: "Notion",
    category: "wiki",
    icon: "🗒️",
    description: "Notion 페이지 트리 BFS 크롤링 (Internal Integration token)",
    status: "available",
    scope: "both",
    userTokenMode: "per-user",
    configSchema: JSON.stringify(
      {
        root_page_id: "abcd1234abcd1234abcd1234abcd1234",
        max_depth: 5,
        include_archived: false,
      },
      null,
      2,
    ),
  },
  {
    id: "gwiki",
    label: "Google Sites",
    category: "wiki",
    icon: "📚",
    description: "Google Sites / Wiki — 로드맵",
    status: "planned",
    scope: "admin",
    userTokenMode: "per-user",
  },

  // ===== Code Repository =====
  {
    id: "git",
    label: "Git Repository",
    category: "code",
    icon: "🔧",
    description: "Git 저장소 (GitHub / GitLab / Bitbucket) markdown 크롤링",
    status: "available",
    scope: "both",
    userTokenMode: "per-user",
    configSchema: JSON.stringify(
      {
        repo_url: "https://github.com/org/repo.git",
        branch: "main",
        include_globs: ["**/*.md"],
      },
      null,
      2,
    ),
  },
  {
    id: "github_issues",
    label: "GitHub Issues",
    category: "code",
    icon: "🐙",
    description: "GitHub Issues / PR 본문 동기화 — 로드맵",
    status: "planned",
    scope: "admin",
    userTokenMode: "per-user",
  },

  // ===== Office Suite =====
  {
    id: "sharepoint",
    label: "SharePoint",
    category: "office",
    icon: "📑",
    description: "Microsoft SharePoint site/list 동기화 (admin app-only token)",
    status: "available",
    scope: "both",
    userTokenMode: "shared",
    configSchema: JSON.stringify(
      {
        site_id: "{hostname},{site-collection-id},{site-id}",
        list_ids: ["list-guid-1", "list-guid-2"],
        max_items: 1000,
      },
      null,
      2,
    ),
  },
  {
    id: "onedrive",
    label: "OneDrive",
    category: "office",
    icon: "☁️",
    description: "OneDrive 파일 동기화 (admin app-only token, PDF/DOCX/PPTX/MD)",
    status: "available",
    scope: "both",
    userTokenMode: "shared",
    configSchema: JSON.stringify(
      {
        drive_path: "users/{user-upn}/drive",
        folder_path: "Documents/2026",
        max_files: 500,
        include_extensions: [".pdf", ".docx", ".pptx", ".md", ".txt"],
      },
      null,
      2,
    ),
  },
  {
    id: "google_drive",
    label: "Google Drive",
    category: "office",
    icon: "📁",
    description: "Google Drive 파일 동기화 — 로드맵",
    status: "planned",
    scope: "admin",
    userTokenMode: "shared",
  },

  // ===== Chat / Communication =====
  {
    id: "slack",
    label: "Slack",
    category: "chat",
    icon: "💬",
    description: "Slack 채널 메시지 + thread 동기화 (admin 등록 bot 사용)",
    status: "available",
    scope: "both",
    userTokenMode: "shared",
    configSchema: JSON.stringify(
      {
        channel_ids: ["C0123ABC", "C0456DEF"],
        days_back: 30,
        include_threads: true,
        include_bot_messages: false,
      },
      null,
      2,
    ),
  },
  {
    id: "teams",
    label: "Microsoft Teams",
    category: "chat",
    icon: "👥",
    description: "Teams 채널 메시지 + thread 동기화 (admin app-only token)",
    status: "available",
    scope: "both",
    userTokenMode: "shared",
    configSchema: JSON.stringify(
      {
        team_id: "team-guid",
        channel_ids: ["19:abc...@thread.tacv2"],
        days_back: 30,
        include_replies: true,
      },
      null,
      2,
    ),
  },

  // ===== Issue Tracker =====
  {
    id: "jira",
    label: "Jira",
    category: "issue",
    icon: "🪪",
    description: "Atlassian Jira 이슈 동기화 — 로드맵",
    status: "planned",
    scope: "admin",
    userTokenMode: "per-user",
  },
];

export const CATEGORY_LABELS: Record<ConnectorCategory, string> = {
  files: "파일 / 스토리지",
  wiki: "Wiki / Documentation",
  code: "Code Repository",
  office: "Office Suite",
  chat: "Chat / Communication",
  issue: "Issue Tracker",
  crawl: "크롤 결과",
};

const CATEGORY_ORDER: ConnectorCategory[] = [
  "files",
  "wiki",
  "code",
  "office",
  "chat",
  "issue",
  "crawl",
];

export const STATUS_BADGE: Record<
  ConnectorStatus,
  { label: string; tone: "success" | "warning" | "neutral" }
> = {
  available: { label: "사용 가능", tone: "success" },
  preview: { label: "프리뷰", tone: "warning" },
  planned: { label: "예정", tone: "neutral" },
};

export type FilterOpts = {
  scope: ConnectorScope;
  search?: string;
  /** false 면 ``planned`` 카드 숨김 — 기본 true (로드맵 시각화). */
  showPlanned?: boolean;
};

export function filterCatalog(opts: FilterOpts): ConnectorEntry[] {
  const { scope, search = "", showPlanned = true } = opts;
  const q = search.trim().toLowerCase();
  return CONNECTOR_CATALOG.filter((c) => {
    if (c.scope !== scope && c.scope !== "both") return false;
    if (!showPlanned && c.status === "planned") return false;
    if (
      q &&
      !c.label.toLowerCase().includes(q) &&
      !c.description.toLowerCase().includes(q) &&
      !c.id.toLowerCase().includes(q)
    ) {
      return false;
    }
    return true;
  });
}

/** 카테고리별 그룹핑 + 정해진 카테고리 순서 — 카탈로그 dialog 가 그대로 렌더. */
export function groupByCategory(
  items: ConnectorEntry[],
): { category: ConnectorCategory; items: ConnectorEntry[] }[] {
  const map = new Map<ConnectorCategory, ConnectorEntry[]>();
  for (const item of items) {
    const arr = map.get(item.category) ?? [];
    arr.push(item);
    map.set(item.category, arr);
  }
  return CATEGORY_ORDER.filter((cat) => map.has(cat)).map((cat) => ({
    category: cat,
    items: map.get(cat) ?? [],
  }));
}

export function findConnector(id: string): ConnectorEntry | undefined {
  return CONNECTOR_CATALOG.find((c) => c.id === id);
}
