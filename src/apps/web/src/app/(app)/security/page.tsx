import { PageShell } from "@/components/layout/PageShell";

export const metadata = { title: "처리방침 · axiomedge" };

export default function SecurityPolicyPage() {
  return (
    <PageShell
      icon="🔒"
      title="처리방침"
      description={
        <>
          axiomedge 가 사용자 데이터를 어떻게 다루는지 요약합니다. 상세 기술
          문서는{" "}
          <code className="rounded bg-bg-muted px-1 py-0.5 font-mono text-xs">
            docs/SECURITY.md
          </code>{" "}
          를 참고하세요.
        </>
      }
    >
      <section id="chat-retention" className="space-y-3">
        <h2 className="text-lg font-semibold">Chat 대화 기록 보존</h2>
        <ul className="ml-5 list-disc space-y-2 text-sm">
          <li>
            <span className="font-medium text-fg-default">보존 기간 90일</span>{" "}
            — 매일 03:20 UTC 자동 파기 (env{" "}
            <code className="rounded bg-bg-muted px-1 py-0.5 font-mono text-xs">
              CHAT_RETENTION_DAYS
            </code>
            ).
          </li>
          <li>
            <span className="font-medium text-fg-default">본인 삭제권 (PIPA §36)</span>{" "}
            — 좌측 sidebar 에서 대화 단위로 즉시 삭제 가능. soft delete 는
            즉시, hard delete 는 다음 cron 사이클.
          </li>
          <li>
            <span className="font-medium text-fg-default">동의 철회권 (PIPA §37)</span>{" "}
            — 좌측 sidebar 하단 프로필 메뉴 → "처리방침 동의 철회". 철회 후
            chat 사용 시 동의 안내 화면이 다시 나타납니다. 기존 대화는 자동
            삭제되지 않으며, 사용자가 sidebar 에서 직접 삭제할 수 있습니다.
          </li>
          <li>
            <span className="font-medium text-fg-default">at-rest 암호화</span>{" "}
            —{" "}
            <code className="rounded bg-bg-muted px-1 py-0.5 font-mono text-xs">
              chat_messages.content_enc
            </code>{" "}
            는 pgp_sym_encrypt 로 암호화 (env{" "}
            <code className="rounded bg-bg-muted px-1 py-0.5 font-mono text-xs">
              CHAT_ENCRYPTION_KEY
            </code>
            ). 프로덕션은 키 누락 시 기동 거부.
          </li>
          <li>
            <span className="font-medium text-fg-default">접근 제어</span> —
            본인 대화만 조회·수정·삭제 가능. 모든 repo 메서드가{" "}
            <code className="rounded bg-bg-muted px-1 py-0.5 font-mono text-xs">
              user_id
            </code>{" "}
            predicate 를 강제.
          </li>
          <li>
            <span className="font-medium text-fg-default">감사 기록</span> —
            대화 생성/이름변경/삭제/메시지 전송은{" "}
            <code className="rounded bg-bg-muted px-1 py-0.5 font-mono text-xs">
              audit_log
            </code>{" "}
            에 기록 (실제 본문 아닌 메타데이터만).
          </li>
        </ul>
      </section>

      <p className="text-sm text-fg-muted">
        처리방침에 동의 후에도 언제든 좌측 sidebar 의 "🚫 처리방침 동의 철회"
        에서 철회할 수 있습니다. 키 분실 / 백업 정책 / 외부 SSO provider 별
        보존 정책 상세는 정보보안팀 문의.
      </p>
    </PageShell>
  );
}
