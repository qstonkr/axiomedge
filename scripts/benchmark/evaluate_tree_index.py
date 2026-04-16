"""Tree Index Before/After 비교 리포트.

기존 rag_eval_results 테이블에서 eval_id 쌍을 조인하여
항목별 델타를 산출하고 CLI 테이블로 출력.

Usage:
    uv run python scripts/evaluate_tree_index.py \\
        --baseline baseline_pageindex \\
        --compare treeindex_p1_20260412

    uv run python scripts/evaluate_tree_index.py \\
        --baseline baseline_pageindex \\
        --compare treeindex_p1_20260412 treeindex_p2_20260415
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import dataclass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class MetricDelta:
    name: str
    before: float
    after: float
    delta: float
    p_value: float | None = None


@dataclass
class CompareReport:
    baseline_id: str
    compare_id: str
    total_items: int
    matched_items: int
    metrics: list[MetricDelta]
    kb_breakdown: dict[str, list[MetricDelta]]
    improved: int  # delta > +0.05
    degraded: int  # delta < -0.05
    unchanged: int


def _get_db_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_db",
    )


async def _load_eval_data(engine, eval_id: str) -> list[dict]:
    """eval_id에 해당하는 평가 결과 로드."""
    from sqlalchemy import text
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT golden_set_id::text, kb_id, question,
                   faithfulness, relevancy, completeness,
                   search_time_ms, crag_action, crag_confidence, recall_hit
            FROM rag_eval_results
            WHERE eval_id = :eid
            ORDER BY golden_set_id
        """), {"eid": eval_id})
        rows = result.mappings().all()
        return [dict(r) for r in rows]


def _compute_metrics(
    baseline_items: list[dict],
    compare_items: list[dict],
) -> CompareReport | None:
    """golden_set_id 기준 1:1 매칭 후 델타 산출."""
    baseline_map = {r["golden_set_id"]: r for r in baseline_items}
    compare_map = {r["golden_set_id"]: r for r in compare_items}

    matched_ids = set(baseline_map.keys()) & set(compare_map.keys())
    if not matched_ids:
        return None

    # 항목별 델타
    metric_names = ["faithfulness", "relevancy", "completeness"]
    deltas: dict[str, list[float]] = {m: [] for m in metric_names}
    deltas["recall"] = []
    deltas["crag_correct"] = []
    deltas["latency"] = []

    kb_deltas: dict[str, dict[str, list[float]]] = {}

    improved = degraded = unchanged = 0

    for gid in sorted(matched_ids):
        b = baseline_map[gid]
        c = compare_map[gid]

        for m in metric_names:
            d = (c.get(m) or 0) - (b.get(m) or 0)
            deltas[m].append(d)

        recall_b = 1.0 if b.get("recall_hit") else 0.0
        recall_c = 1.0 if c.get("recall_hit") else 0.0
        deltas["recall"].append(recall_c - recall_b)

        crag_b = 1.0 if b.get("crag_action") == "correct" else 0.0
        crag_c = 1.0 if c.get("crag_action") == "correct" else 0.0
        deltas["crag_correct"].append(crag_c - crag_b)

        deltas["latency"].append((c.get("search_time_ms") or 0) - (b.get("search_time_ms") or 0))

        # Completeness 기준 개선/악화 판단
        comp_delta = (c.get("completeness") or 0) - (b.get("completeness") or 0)
        if comp_delta > 0.05:
            improved += 1
        elif comp_delta < -0.05:
            degraded += 1
        else:
            unchanged += 1

        # KB별 분해
        kb = b.get("kb_id", "unknown")
        if kb not in kb_deltas:
            kb_deltas[kb] = {m: [] for m in [*metric_names, "recall", "crag_correct"]}
        for m in metric_names:
            kb_deltas[kb][m].append((c.get(m) or 0) - (b.get(m) or 0))
        kb_deltas[kb]["recall"].append(recall_c - recall_b)
        kb_deltas[kb]["crag_correct"].append(crag_c - crag_b)

    # 통계 검정 (paired t-test)
    metrics_result = []
    for m in [*metric_names, "recall", "crag_correct", "latency"]:
        vals = deltas[m]
        n = len(vals)
        mean_b = _mean([baseline_map[gid].get(m, 0) for gid in sorted(matched_ids)]
                       if m in metric_names else
                       [1.0 if baseline_map[gid].get("recall_hit") else 0.0 for gid in sorted(matched_ids)]
                       if m == "recall" else
                       [1.0 if baseline_map[gid].get("crag_action") == "correct" else 0.0
                        for gid in sorted(matched_ids)]
                       if m == "crag_correct" else
                       [baseline_map[gid].get("search_time_ms", 0) for gid in sorted(matched_ids)])
        mean_a = _mean([compare_map[gid].get(m, 0) for gid in sorted(matched_ids)]
                       if m in metric_names else
                       [1.0 if compare_map[gid].get("recall_hit") else 0.0 for gid in sorted(matched_ids)]
                       if m == "recall" else
                       [1.0 if compare_map[gid].get("crag_action") == "correct" else 0.0
                        for gid in sorted(matched_ids)]
                       if m == "crag_correct" else
                       [compare_map[gid].get("search_time_ms", 0) for gid in sorted(matched_ids)])

        p_value = _paired_ttest(vals) if n >= 5 else None
        metrics_result.append(MetricDelta(
            name=m, before=mean_b, after=mean_a,
            delta=mean_a - mean_b, p_value=p_value,
        ))

    # KB별 요약
    kb_breakdown: dict[str, list[MetricDelta]] = {}
    for kb, kd in kb_deltas.items():
        kb_metrics = []
        for m in [*metric_names, "recall", "crag_correct"]:
            vals = kd[m]
            kb_metrics.append(MetricDelta(
                name=m, before=0, after=0,
                delta=_mean(vals), p_value=None,
            ))
        kb_breakdown[kb] = kb_metrics

    return CompareReport(
        baseline_id="", compare_id="",
        total_items=len(baseline_items),
        matched_items=len(matched_ids),
        metrics=metrics_result,
        kb_breakdown=kb_breakdown,
        improved=improved, degraded=degraded, unchanged=unchanged,
    )


def _mean(vals: list[float]) -> float:
    return sum(vals) / max(len(vals), 1)


def _paired_ttest(deltas: list[float]) -> float | None:
    """Paired t-test p-value (단일 표본 t-test on deltas)."""
    try:
        from scipy import stats
        if len(deltas) < 2:
            return None
        t_stat, p_value = stats.ttest_1samp(deltas, 0)
        return float(p_value)
    except ImportError:
        # scipy 없으면 수동 계산
        import math
        n = len(deltas)
        if n < 2:
            return None
        mean_d = sum(deltas) / n
        var_d = sum((d - mean_d) ** 2 for d in deltas) / (n - 1)
        if var_d == 0:
            return 0.0
        t_stat = mean_d / math.sqrt(var_d / n)
        # 근사 p-value (정규 분포 근사, n>=30에서 유효)
        try:
            from scipy.stats import t
            return float(2 * (1 - t.cdf(abs(t_stat), n - 1)))
        except ImportError:
            return None


def _format_report(report: CompareReport) -> str:
    """CLI 테이블 포맷."""
    lines = []
    lines.append("\n=== Tree Index Before/After 비교 ===")
    lines.append(f"기준: {report.baseline_id} vs {report.compare_id}")
    lines.append(f"매칭 항목: {report.matched_items}/{report.total_items}")
    lines.append("")

    # 메인 테이블
    lines.append(f"{'지표':<16} {'Before':>8} {'After':>8} {'Delta':>8} {'p-value':>9}")
    lines.append("-" * 55)

    for m in report.metrics:
        sig = ""
        if m.p_value is not None:
            if m.p_value < 0.01:
                sig = "**"
            elif m.p_value < 0.05:
                sig = "*"

        if m.name == "latency":
            lines.append(
                f"{m.name:<16} {m.before:>7.0f}ms {m.after:>7.0f}ms {m.delta:>+7.0f}ms {'':>9}"
            )
        elif m.name in ("recall", "crag_correct"):
            lines.append(
                f"{m.name:<16} {m.before * 100:>7.1f}% {m.after * 100:>7.1f}% "
                f"{m.delta * 100:>+6.1f}%p "
                f"{m.p_value:>7.4f}{sig}" if m.p_value else
                f"{m.name:<16} {m.before * 100:>7.1f}% {m.after * 100:>7.1f}% "
                f"{m.delta * 100:>+6.1f}%p {'N/A':>9}"
            )
        else:
            lines.append(
                f"{m.name:<16} {m.before:>8.3f} {m.after:>8.3f} {m.delta:>+8.3f} "
                + (f"{m.p_value:>7.4f}{sig}" if m.p_value else f"{'N/A':>9}")
            )

    lines.append("")
    lines.append(
        f"개선: {report.improved}/{report.matched_items} ({report.improved * 100 / max(report.matched_items, 1):.0f}%)  |  "
        f"악화: {report.degraded}/{report.matched_items} ({report.degraded * 100 / max(report.matched_items, 1):.0f}%)  |  "
        f"동일: {report.unchanged}/{report.matched_items}"
    )

    # KB별 분해
    if report.kb_breakdown:
        lines.append("")
        lines.append("--- KB별 평균 Delta ---")
        lines.append(f"{'KB':<18} {'Faith':>7} {'Relev':>7} {'Compl':>7} {'Recall':>8} {'CRAG':>7}")
        lines.append("-" * 58)
        for kb, metrics in sorted(report.kb_breakdown.items()):
            vals = [m.delta for m in metrics]
            lines.append(
                f"{kb:<18} {vals[0]:>+7.3f} {vals[1]:>+7.3f} {vals[2]:>+7.3f} "
                f"{vals[3] * 100:>+6.1f}%p {vals[4] * 100:>+5.1f}%p"
            )

    # Go/No-Go 판단
    lines.append("")
    go, reasons = _check_go_nogo(report)
    for reason in reasons:
        lines.append(reason)

    return "\n".join(lines)


def _check_go_nogo(report: CompareReport) -> tuple[bool, list[str]]:
    """Go/No-Go 기준 판단. Returns (통과 여부, 사유 메시지 리스트)."""
    comp = next((m for m in report.metrics if m.name == "completeness"), None)
    faith = next((m for m in report.metrics if m.name == "faithfulness"), None)
    lat = next((m for m in report.metrics if m.name == "latency"), None)

    go = True
    reasons = []
    if comp and comp.delta < 0.05:
        reasons.append(f"⚠ Go/No-Go: Completeness Δ ({comp.delta:+.3f}) < +0.05 목표 미달")
        go = False
    if faith and faith.delta < 0:
        reasons.append(f"⚠ Go/No-Go: Faithfulness 악화 ({faith.delta:+.3f})")
        go = False
    if lat and lat.delta > 200:
        reasons.append(f"⚠ Go/No-Go: Latency 증가 ({lat.delta:+.0f}ms) > 200ms 허용치 초과")
        go = False
    if go:
        reasons.append("✓ Go/No-Go: 모든 기준 충족 — 배포 가능")

    return go, reasons


async def _promote_baseline(engine, old_baseline_id: str, new_eval_id: str):
    """새 eval_id의 결과를 공식 베이스라인으로 승격.

    1. 기존 베이스라인을 백업 (baseline_pageindex → baseline_pageindex_prev_YYYYMMDD)
    2. 새 eval_id를 베이스라인으로 복사 (new_eval_id → baseline_pageindex)
    """
    from datetime import datetime
    from sqlalchemy import text

    backup_id = f"{old_baseline_id}_prev_{datetime.now().strftime('%Y%m%d_%H%M')}"

    async with engine.begin() as conn:
        # 기존 베이스라인 백업
        await conn.execute(text("""
            UPDATE rag_eval_results
            SET eval_id = :backup_id
            WHERE eval_id = :old_id
        """), {"backup_id": backup_id, "old_id": old_baseline_id})

        # 새 eval_id를 베이스라인 이름으로 복사
        await conn.execute(text("""
            INSERT INTO rag_eval_results
                (eval_id, kb_id, golden_set_id, question, expected_answer,
                 actual_answer, faithfulness, relevancy, completeness,
                 search_time_ms, crag_action, crag_confidence,
                 recall_hit, crag_recommendation, created_at)
            SELECT :new_baseline_id, kb_id, golden_set_id, question, expected_answer,
                   actual_answer, faithfulness, relevancy, completeness,
                   search_time_ms, crag_action, crag_confidence,
                   recall_hit, crag_recommendation, created_at
            FROM rag_eval_results
            WHERE eval_id = :source_id
        """), {"new_baseline_id": old_baseline_id, "source_id": new_eval_id})

    logger.info(
        "Baseline promoted: '%s' → backup '%s', '%s' → new '%s'",
        old_baseline_id, backup_id, new_eval_id, old_baseline_id,
    )


async def async_main(baseline_id: str, compare_ids: list[str], promote_if_better: bool = False):
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_get_db_url())
    try:
        baseline_data = await _load_eval_data(engine, baseline_id)
        if not baseline_data:
            logger.error("Baseline '%s' not found in rag_eval_results", baseline_id)
            return

        logger.info("Baseline '%s': %d items", baseline_id, len(baseline_data))

        best_candidate: tuple[str, CompareReport] | None = None

        for cid in compare_ids:
            compare_data = await _load_eval_data(engine, cid)
            if not compare_data:
                logger.warning("Compare '%s' not found, skipping", cid)
                continue

            logger.info("Compare '%s': %d items", cid, len(compare_data))

            report = _compute_metrics(baseline_data, compare_data)
            if not report:
                logger.warning("No matching items between '%s' and '%s'", baseline_id, cid)
                continue

            report.baseline_id = baseline_id
            report.compare_id = cid
            print(_format_report(report))

            go, _ = _check_go_nogo(report)
            if go:
                comp = next((m for m in report.metrics if m.name == "completeness"), None)
                comp_delta = comp.delta if comp else 0
                if best_candidate is None or comp_delta > best_candidate[1].metrics[2].delta:
                    best_candidate = (cid, report)

        # 승격 처리
        if promote_if_better and best_candidate:
            winner_id, winner_report = best_candidate
            print(f"\n>>> 승격 대상: {winner_id} (Go/No-Go 통과)")
            print(f">>> 기존 '{baseline_id}'를 백업 후 '{winner_id}' 결과를 새 베이스라인으로 승격합니다.")
            await _promote_baseline(engine, baseline_id, winner_id)
            print(f">>> 승격 완료. 새 베이스라인: {baseline_id}")
        elif promote_if_better:
            print("\n>>> 승격 대상 없음: Go/No-Go 기준을 충족하는 eval_id가 없습니다.")
            print(">>> 기존 베이스라인 유지.")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tree Index Before/After 비교 리포트")
    parser.add_argument("--baseline", required=True, help="Baseline eval_id")
    parser.add_argument("--compare", nargs="+", required=True, help="비교 대상 eval_id(s)")
    parser.add_argument(
        "--promote-if-better", action="store_true",
        help="Go/No-Go 기준 충족 시 새 eval_id를 베이스라인으로 승격",
    )
    args = parser.parse_args()

    asyncio.run(async_main(args.baseline, args.compare, args.promote_if_better))
