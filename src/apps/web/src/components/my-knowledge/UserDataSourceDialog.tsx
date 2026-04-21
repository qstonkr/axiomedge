"use client";

import { useState, type FormEvent } from "react";

import {
  Button,
  Dialog,
  Input,
  Select,
  Textarea,
  useToast,
} from "@/components/ui";
import { createUserDataSource, type UserDataSourceCreateBody } from "@/lib/api/endpoints";
import type { ConnectorEntry } from "@/lib/connectors/catalog";

const SCHEDULES = ["수동", "hourly", "daily", "weekly"];

type Props = {
  open: boolean;
  kbId: string;
  connector: ConnectorEntry;
  onClose: () => void;
  onCreated: () => void;
};

/**
 * 사용자 self-service 등록 dialog.
 *
 * - per-user token 모드: password input 노출. 사용자가 본인 PAT 입력.
 * - shared token 모드: token input hidden + "관리자 등록 bot 사용" 안내.
 *   사용자는 channel_ids 등 sub-resource 만 입력.
 * - none 모드: 토큰 영역 자체 비활성 (file_upload — 이 dialog 안 통과).
 */
export function UserDataSourceDialog({
  open, kbId, connector, onClose, onCreated,
}: Props) {
  const toast = useToast();
  const [name, setName] = useState(`내 ${connector.label}`);
  const [schedule, setSchedule] = useState("수동");
  const [crawlConfigText, setCrawlConfigText] = useState("");
  const [secretToken, setSecretToken] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const isPerUser = connector.userTokenMode === "per-user";
  const isShared = connector.userTokenMode === "shared";

  async function submit(e: FormEvent) {
    e.preventDefault();

    let crawl_config: Record<string, unknown> | null = null;
    try {
      crawl_config = crawlConfigText.trim()
        ? JSON.parse(crawlConfigText)
        : null;
    } catch {
      toast.push("crawl_config 가 유효한 JSON 이 아닙니다.", "danger");
      return;
    }

    if (isPerUser && !secretToken.trim()) {
      toast.push(
        "토큰을 입력해주세요 — 본인 권한 안에서만 데이터를 가져옵니다.",
        "warning",
      );
      return;
    }

    const body: UserDataSourceCreateBody = {
      name: name.trim(),
      source_type: connector.id,
      schedule: schedule === "수동" ? null : schedule,
      crawl_config,
    };
    if (isPerUser && secretToken.trim()) {
      body.secret_token = secretToken.trim();
    }

    setSubmitting(true);
    try {
      await createUserDataSource(kbId, body);
      toast.push(`${connector.label} 소스가 추가됐습니다.`, "success");
      onCreated();
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "등록 실패";
      toast.push(msg, "danger");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`${connector.icon} ${connector.label} 추가`}
      description={connector.description}
      width="lg"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button
            type="submit"
            form="user-ds-form"
            disabled={submitting || !name.trim()}
          >
            {submitting ? "추가 중…" : "추가"}
          </Button>
        </>
      }
    >
      <form id="user-ds-form" onSubmit={submit} className="space-y-3">
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          이름
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            autoFocus
          />
        </label>

        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          스케줄
          <Select
            value={schedule}
            onChange={(e) => setSchedule(e.target.value)}
          >
            {SCHEDULES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </label>

        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          crawl_config (JSON)
          <Textarea
            value={crawlConfigText}
            onChange={(e) => setCrawlConfigText(e.target.value)}
            rows={isShared ? 4 : 6}
            placeholder={connector.configSchema}
            className="font-mono text-xs"
          />
          <span className="text-[10px] text-fg-subtle">
            {isShared
              ? "이 connector 는 channel_ids 등 sub-resource 만 입력합니다 — 토큰은 관리자가 등록한 bot 사용."
              : "⚠️ token / PAT / password 는 절대 여기에 넣지 마세요 — 아래 전용 입력 사용."}
          </span>
        </label>

        {isPerUser && (
          <fieldset className="space-y-2 rounded-md border border-warning-default/30 bg-warning-subtle/40 p-3">
            <legend className="px-1 text-xs font-semibold text-warning-default">
              🔐 인증 토큰 (본인 PAT)
            </legend>
            <p className="text-[11px] text-fg-muted">
              본인이 발급한 토큰을 입력합니다. 본인 권한 안에서만 데이터를
              가져오므로 권한 외 정보는 자동으로 차단됩니다. 입력값은 즉시
              암호화 저장되며 응답에 노출되지 않습니다.
            </p>
            <Input
              type="password"
              value={secretToken}
              onChange={(e) => setSecretToken(e.target.value)}
              placeholder="본인 PAT 입력"
              autoComplete="new-password"
            />
          </fieldset>
        )}

        {isShared && (
          <fieldset className="space-y-2 rounded-md border border-accent-default/30 bg-accent-subtle/40 p-3">
            <legend className="px-1 text-xs font-semibold text-accent-default">
              🤝 관리자 등록 bot 사용
            </legend>
            <p className="text-[11px] text-fg-muted">
              {connector.label} 은 워크스페이스 단위 인증이 필요해, 관리자가
              한 번 등록한 bot 토큰을 모든 사용자가 공유합니다. 사용자는
              필요한 channel/sub-resource ID 만 입력하면 됩니다. bot 이
              초대된 채널만 동기화됩니다.
            </p>
          </fieldset>
        )}
      </form>
    </Dialog>
  );
}
