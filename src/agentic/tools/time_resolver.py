"""Time resolver tool — '차주', '지난 주 월요일' 등 상대 시점 → 절대 날짜.

기존 query_preprocessor._resolve_relative_time 의 규칙 부분 재사용.
state-free pure tool — KST(UTC+9) 기준.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from src.agentic.protocols import Tool, ToolResult

logger = logging.getLogger(__name__)


_KST = timezone(timedelta(hours=9))

# 상대 표현 → resolver lambda
_TIME_RULES: dict[str, Any] = {
    "차주": lambda n: _week_label(n + timedelta(weeks=1)),
    "다음 주": lambda n: _week_label(n + timedelta(weeks=1)),
    "이번 주": lambda n: _week_label(n),
    "금주": lambda n: _week_label(n),
    "지난 주": lambda n: _week_label(n - timedelta(weeks=1)),
    "전주": lambda n: _week_label(n - timedelta(weeks=1)),
    "이번 달": lambda n: f"{n.year}년 {n.month}월",
    "이번달": lambda n: f"{n.year}년 {n.month}월",
    "지난 달": lambda n: _last_month_label(n),
    "지난달": lambda n: _last_month_label(n),
    "다음 달": lambda n: _next_month_label(n),
    "어제": lambda n: _date_label(n - timedelta(days=1)),
    "오늘": lambda n: _date_label(n),
    "내일": lambda n: _date_label(n + timedelta(days=1)),
    "그제": lambda n: _date_label(n - timedelta(days=2)),
    "그저께": lambda n: _date_label(n - timedelta(days=2)),
    "모레": lambda n: _date_label(n + timedelta(days=2)),
}


def _week_label(d: datetime) -> str:
    week_of_month = (d.day - 1) // 7 + 1
    return f"{d.year}년 {d.month}월 {week_of_month}주차"


def _date_label(d: datetime) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일"


def _last_month_label(n: datetime) -> str:
    last = n.replace(day=1) - timedelta(days=1)
    return f"{last.year}년 {last.month}월"


def _next_month_label(n: datetime) -> str:
    nxt = n.replace(day=28) + timedelta(days=4)
    return f"{nxt.year}년 {nxt.month}월"


# Pattern 'N 일 전', 'N 주 전', 'N 달 전'
_RELATIVE_N_RE = re.compile(r"(\d+)\s*([일주달])\s*전")


class TimeResolverTool(Tool):
    name = "time_resolver"
    description = (
        "한국어 상대 시점 표현 ('차주', '지난 주', '3일 전' 등) 을 절대 날짜로 변환. "
        "검색 query 에 시간 필터가 필요할 때 첫 단계로 호출."
    )
    args_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "변환할 시점 표현 (예: '차주', '지난 주 월요일')"},
        },
        "required": ["expression"],
    }

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:  # noqa: ARG002
        expr = args.get("expression", "").strip()
        if not expr:
            return ToolResult(success=False, data=None, error="expression is required")

        now = datetime.now(_KST)

        # 단순 매핑 우선
        for keyword, resolver in _TIME_RULES.items():
            if keyword in expr:
                resolved = resolver(now)
                return ToolResult(
                    success=True, data={"original": expr, "resolved": resolved, "rule": keyword},
                    metadata={"now_kst": now.isoformat()},
                )

        # N 일/주/달 전
        m = _RELATIVE_N_RE.search(expr)
        if m:
            n_str, unit = m.group(1), m.group(2)
            n = int(n_str)
            if unit == "일":
                target = now - timedelta(days=n)
                resolved = _date_label(target)
            elif unit == "주":
                target = now - timedelta(weeks=n)
                resolved = _week_label(target)
            else:  # "달"
                month_offset = n
                year = now.year
                month = now.month - month_offset
                while month <= 0:
                    month += 12
                    year -= 1
                resolved = f"{year}년 {month}월"
            return ToolResult(
                success=True, data={"original": expr, "resolved": resolved, "rule": f"{n}{unit} 전"},
                metadata={"now_kst": now.isoformat()},
            )

        return ToolResult(
            success=True, data={"original": expr, "resolved": expr, "rule": "no-op"},
            metadata={"now_kst": now.isoformat(), "matched": False},
        )
