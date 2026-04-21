"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Dialog, Input, Skeleton, useToast } from "@/components/ui";
import {
  deleteSharedToken,
  listSharedTokens,
  upsertSharedToken,
  type SharedTokenItem,
} from "@/lib/api/endpoints";
import { findConnector } from "@/lib/connectors/catalog";

type Props = {
  open: boolean;
  onClose: () => void;
};

/**
 * Admin shared-token CRUD dialog.
 *
 * Slack 같은 organization-wide bot token 을 admin 이 등록 — 사용자가
 * self-service 로 ``slack`` source 추가하면 launcher 가 본 token 자동 fetch.
 * 응답에 token 값 노출 X — 등록 여부 (`configured: true/false`) 만.
 */
export function SharedTokensDialog({ open, onClose }: Props) {
  const toast = useToast();
  const [items, setItems] = useState<SharedTokenItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [editingFor, setEditingFor] = useState<string | null>(null);
  const [newToken, setNewToken] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listSharedTokens());
    } catch (e) {
      toast.push(
        e instanceof Error ? e.message : "shared-token 목록 조회 실패",
        "danger",
      );
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      // open 토글 시 상태 reset + 최신 데이터 fetch — async 안에서 set 호출이라
      // react-hooks/set-state-in-effect 회피.
      setEditingFor(null);
      setNewToken("");
      setLoading(true);
      try {
        const data = await listSharedTokens();
        if (!cancelled) setItems(data);
      } catch (e) {
        if (!cancelled) {
          toast.push(
            e instanceof Error ? e.message : "shared-token 목록 조회 실패",
            "danger",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, toast]);

  async function onSubmitToken(connectorId: string) {
    if (!newToken.trim()) {
      toast.push("토큰을 입력해주세요.", "warning");
      return;
    }
    setBusy(true);
    try {
      await upsertSharedToken(connectorId, newToken.trim());
      toast.push(`${connectorId} bot token 이 등록됐습니다.`, "success");
      setEditingFor(null);
      setNewToken("");
      await refresh();
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "등록 실패", "danger");
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteToken(connectorId: string) {
    if (!confirm(`${connectorId} 의 shared bot token 을 삭제하시겠습니까?\n이후 모든 사용자의 ${connectorId} source 동기화가 명시적으로 실패합니다.`))
      return;
    setBusy(true);
    try {
      await deleteSharedToken(connectorId);
      toast.push(`${connectorId} bot token 이 삭제됐습니다.`, "success");
      await refresh();
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="🤝 공유 Bot 토큰 (organization-wide)"
      description="Slack/Teams 같은 워크스페이스 bot 토큰을 admin 이 1회 등록하면, 모든 사용자가 self-service 로 channel/sub-resource 만 입력해 source 추가할 수 있습니다. 토큰 값은 응답에 노출되지 않습니다."
      width="lg"
      footer={
        <Button type="button" variant="ghost" onClick={onClose}>
          닫기
        </Button>
      }
    >
      {loading || items === null ? (
        <Skeleton className="h-32" />
      ) : items.length === 0 ? (
        <p className="text-sm text-fg-muted">
          등록 가능한 shared-token connector 가 없습니다.
        </p>
      ) : (
        <div className="space-y-3">
          {items.map((item) => {
            const connector = findConnector(item.connector_id);
            const isEditing = editingFor === item.connector_id;
            return (
              <div
                key={item.connector_id}
                className="flex flex-col gap-2 rounded-md border border-border-default bg-bg-default p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xl" aria-hidden>
                      {connector?.icon ?? "🤖"}
                    </span>
                    <div>
                      <div className="text-sm font-medium text-fg-default">
                        {connector?.label ?? item.connector_id}
                      </div>
                      <code className="text-[10px] text-fg-subtle">
                        {item.connector_id}
                      </code>
                    </div>
                  </div>
                  <Badge tone={item.configured ? "success" : "neutral"}>
                    {item.configured ? "등록됨" : "미등록"}
                  </Badge>
                </div>

                {isEditing ? (
                  <div className="space-y-2">
                    <Input
                      type="password"
                      placeholder={`${item.connector_id} bot token 입력`}
                      value={newToken}
                      onChange={(e) => setNewToken(e.target.value)}
                      autoComplete="new-password"
                      autoFocus
                    />
                    <div className="flex justify-end gap-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditingFor(null);
                          setNewToken("");
                        }}
                        disabled={busy}
                      >
                        취소
                      </Button>
                      <Button
                        size="sm"
                        onClick={() => onSubmitToken(item.connector_id)}
                        disabled={busy || !newToken.trim()}
                      >
                        {busy ? "저장 중…" : "저장"}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex justify-end gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setEditingFor(item.connector_id);
                        setNewToken("");
                      }}
                    >
                      {item.configured ? "교체" : "등록"}
                    </Button>
                    {item.configured && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => onDeleteToken(item.connector_id)}
                        disabled={busy}
                      >
                        삭제
                      </Button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Dialog>
  );
}
