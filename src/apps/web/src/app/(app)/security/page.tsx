export const metadata = { title: "처리방침 · axiomedge" };

export default function SecurityPolicyPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="text-xl font-semibold">처리방침</h1>
      <p className="mt-2 text-sm text-fg-muted">
        axiomedge 가 사용자 데이터를 어떻게 다루는지 요약합니다. 상세 기술
        문서는 <code>docs/SECURITY.md</code> 를 참고하세요.
      </p>

      <section id="chat-retention" className="mt-8 space-y-3">
        <h2 className="text-base font-semibold">Chat 대화 기록 보존</h2>
        <ul className="ml-5 list-disc space-y-1 text-sm">
          <li>
            <b>보존 기간 90일</b> — 매일 03:20 UTC 자동 파기 (env{" "}
            <code>CHAT_RETENTION_DAYS</code>).
          </li>
          <li>
            <b>본인 삭제권</b> (PIPA §36) — 좌측 sidebar 에서 대화 단위로
            즉시 삭제 가능. soft delete 는 즉시, hard delete 는 다음 cron
            사이클.
          </li>
          <li>
            <b>at-rest 암호화</b> — <code>chat_messages.content_enc</code> 는
            pgp_sym_encrypt 로 암호화 (env <code>CHAT_ENCRYPTION_KEY</code>).
            프로덕션은 키 누락 시 기동 거부.
          </li>
          <li>
            <b>접근 제어</b> — 본인 대화만 조회·수정·삭제 가능. 모든 repo
            메서드가 <code>user_id</code> predicate 를 강제.
          </li>
          <li>
            <b>감사 기록</b> — 대화 생성/이름변경/삭제/메시지 전송은{" "}
            <code>audit_log</code> 에 기록 (실제 본문 아닌 메타데이터만).
          </li>
        </ul>
      </section>

      <section className="mt-8 space-y-2 text-sm text-fg-muted">
        <p>
          처리방침에 동의 후에도 언제든 좌측 sidebar 에서 대화를 직접 삭제하실
          수 있습니다. 키 분실 / 백업 정책 / 외부 SSO provider 별 보존 정책
          상세는 정보보안팀 문의.
        </p>
      </section>
    </div>
  );
}
