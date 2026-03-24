"""Knowledge Dashboard Local - Environment Configuration (SSOT)

Adapted from oreo-ecosystem knowledge-dashboard for standalone local use.
All API calls go to a single local FastAPI server at DASHBOARD_API_URL.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# --- Local FastAPI server (single endpoint) ---
DASHBOARD_API_URL = os.getenv("DASHBOARD_API_URL", "http://localhost:8000")


def _safe_int(env_key: str, default: int) -> int:
    """Parse env var as int with fallback."""
    raw = os.getenv(env_key, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


API_TIMEOUT = _safe_int("API_TIMEOUT", 30)
API_SEARCH_TIMEOUT = _safe_int("API_SEARCH_TIMEOUT", 120)
API_RETRY_COUNT = _safe_int("API_RETRY_COUNT", 1)  # Simplified for local

# --- Qdrant (Vector DB) ---
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

# --- Neo4j (Graph DB) ---
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "knowledge-graph")

# --- User ID -> Name Mapping ---
USER_ID_NAME_MAP: dict[str, str] = {
    "mslee": "이명석",
    "hbkim": "김현빈",
    "hwlee": "이한울",
    "jk.min": "민재경",
    "moonjr": "문정록",
    "sa10484": "김성아",
    "zeross": "제로스",
    "yohan": "요한",
    "jang.j": "장진",
    "jihoonlim": "임지훈",
    "kim.se": "김세영",
    "hw.lee": "이한울",
    "22980": "사번22980",
    "hanuk": "한욱",
    "youwd": "유원득",
    "spd3399": "스페이드",
    "jsjung": "정진수",
    "jeong.sj": "정성진",
    "deokmoon": "덕문",
    "kimhk": "김현기",
    "choi.jc": "최종찬",
    "jwkim": "김정원",
    "lee.sumi": "이수미",
}
