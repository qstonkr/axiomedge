"use client";

import type { ReactNode } from "react";
import { RotateCcw } from "lucide-react";

import { Button } from "./Button";

/**
 * 표준 query/data 로드 실패 fallback. 모든 사용자/admin 페이지가 같은
 * UX 로 에러를 보여주도록 통일.
 *
 * - 좌측 accent strip (danger tone)
 * - 사용자가 알아볼 한국어 타이틀 + 기술적 message (font-mono) 분리
 * - retry 버튼 (provided 시) — TanStack Query 의 `refetch` 같은 함수 받음
 * - 부분 실패가 있으면 `partial_errors` 로 expander 노출 (find-owner 패턴)
 */
export function ErrorFallback({
  title = "데이터를 불러올 수 없습니다",
  description,
  error,
  onRetry,
  partialErrors,
  className,
}: {
  title?: string;
  /** 사용자에게 보일 한 줄 설명. 생략 시 default 메시지. */
  description?: ReactNode;
  /** Error 객체 또는 message 문자열. 기술적 디버깅 정보. */
  error?: unknown;
  /** Retry 버튼이 호출할 함수. 보통 query.refetch. */
  onRetry?: () => void;
  /** 부분 실패 source 들 (예: ["graph: timeout", "document_owner: 503"]) */
  partialErrors?: string[];
  className?: string;
}) {
  const errMsg =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : null;

  return (
    <div
      role="alert"
      className={[
        "rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm",
        className ?? "",
      ].join(" ")}
    >
      <div className="mb-2 font-medium text-danger-default">{title}</div>
      {description && (
        <p className="mb-2 text-fg-default">{description}</p>
      )}
      {errMsg && (
        <p className="break-words font-mono text-xs text-fg-muted">{errMsg}</p>
      )}
      {partialErrors && partialErrors.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-medium text-warning-default">
            일부 source 실패 ({partialErrors.length}건)
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 font-mono text-xs text-fg-muted">
            {partialErrors.map((e, i) => (
              <li key={i} className="break-words">
                {e}
              </li>
            ))}
          </ul>
        </details>
      )}
      {onRetry && (
        <div className="mt-3">
          <Button
            size="sm"
            variant="ghost"
            leftIcon={<RotateCcw size={14} strokeWidth={1.75} aria-hidden />}
            onClick={onRetry}
          >
            다시 시도
          </Button>
        </div>
      )}
    </div>
  );
}
