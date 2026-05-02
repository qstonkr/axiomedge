/**
 * 글로벌 teardown — 테스트 크래시로 남은 `e2e-*` 데이터 SQL 일괄 삭제.
 *
 * 각 spec 이 self-cleanup (UI delete) 하지만 mid-flow 실패 시 잔존 가능.
 * 이 hook 이 마지막 안전망. docker compose 의 postgres 컨테이너에 직접
 * psql 던짐 — node-pg 의존성 없이 가볍게.
 */
import { execSync } from "node:child_process";

export default async function globalTeardown(): Promise<void> {
  // CI / no-docker 환경에서는 skip
  let pgContainer: string;
  try {
    pgContainer = execSync(
      'docker ps --format "{{.Names}}" | grep postgres | head -1',
      { encoding: "utf-8" },
    ).trim();
  } catch {
    console.log("[e2e teardown] postgres container 못 찾음 — skip");
    return;
  }
  if (!pgContainer) {
    console.log("[e2e teardown] postgres container 못 찾음 — skip");
    return;
  }

  const sql = `
    DELETE FROM glossary_term_relations WHERE child_id IN (SELECT id FROM glossary_terms WHERE term LIKE 'e2e-%') OR parent_id IN (SELECT id FROM glossary_terms WHERE term LIKE 'e2e-%');
    DELETE FROM glossary_terms WHERE term LIKE 'e2e-%';
    DELETE FROM kb_search_groups WHERE name LIKE 'e2e-%';
    DELETE FROM knowledge_data_sources WHERE name LIKE 'e2e-%';
    DELETE FROM rag_golden_set WHERE question LIKE 'e2e-%';
    DELETE FROM document_error_reports WHERE description LIKE 'e2e-%';
    DELETE FROM distill_edge_servers WHERE store_id LIKE 'e2e-%';
    SELECT
      (SELECT count(*) FROM glossary_terms WHERE term LIKE 'e2e-%') as glossary_remaining,
      (SELECT count(*) FROM kb_search_groups WHERE name LIKE 'e2e-%') as groups_remaining,
      (SELECT count(*) FROM knowledge_data_sources WHERE name LIKE 'e2e-%') as sources_remaining;
  `;

  try {
    const out = execSync(
      `docker exec -i ${pgContainer} psql -U knowledge -d knowledge_db -c "${sql.replace(/\n/g, " ")}"`,
      { encoding: "utf-8" },
    );
    console.log(`[e2e teardown] cleanup OK\n${out.split("\n").slice(-6).join("\n")}`);
  } catch (e) {
    console.error(`[e2e teardown] SQL cleanup 실패 — orphan 데이터 잔존 가능: ${e instanceof Error ? e.message : e}`);
  }
}
