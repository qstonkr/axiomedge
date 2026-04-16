"""Distill 시드 데이터.

앱 스타트업 시 베이스 모델 레지스트리의 기본 행을 **insert-only** 로 삽입.
테이블이 없을 때 빈 상태로 시작하면 대시보드 드롭다운이 비게 되므로, 코드
배포와 함께 신규 hf_id 가 자동으로 맞춰지도록 한다.

**중요**: seed 는 ``INSERT ... ON CONFLICT DO NOTHING`` 을 사용 — 이미 존재하는
행 (관리자가 Admin UI 에서 편집한 ``verified`` / ``notes`` 등) 은 **절대 덮어쓰지
않는다**. Admin 이 명시적으로 update 를 원하면 Admin UI 의 편집 폼이 ``upsert``
API 를 호출한다.

즉 seed 의 역할은 "새 버전에 추가된 모델을 기존 DB 에 끼워넣는 migration".
"""

from __future__ import annotations

import logging
from typing import Any

from src.distill.repository import DistillRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 기본 레지스트리 — 추가/제거/수정 시 여기만 고치면 됨
# ---------------------------------------------------------------------------
# 1B 급 (Qwen2.5-0.5B/1.5B, Gemma 3 1B) 은 한국어 factual QA 구조적 한계
# (Gemma 3 1B Global MMLU-Lite 24.9, 랜덤 수준) 확인돼서 전부 제외.
# 4B+ / 한국어 특화 모델만 유지.

DEFAULT_BASE_MODELS: list[dict[str, Any]] = [
    # default — 상업 OK 중 한국어 품질 충분한 유일한 모델. 드롭다운 첫 항목
    # 이어야 non-commercial 모델이 실수로 기본 선택되는 사고 방지.
    {
        "hf_id": "google/gemma-3-4b-it",
        "display_name": "Gemma 3 4B it",
        "params": "4B",
        "license": "Gemma",
        "commercial_use": True,
        "verified": True,
        "notes": "default · 상업 OK · Gemma3ForConditionalGeneration (multimodal) text tower 자동 추출",
        "enabled": True,
        "sort_order": 10,
    },
    {
        "hf_id": "kakaocorp/kanana-nano-2.1b-instruct",
        "display_name": "Kakao Kanana Nano 2.1B",
        "params": "2.1B",
        "license": "Kanana (재확인 필요)",
        # 상업 여부 불확실 — False 로 보수적 설정. 확인 후 True 로 승격 가능.
        "commercial_use": False,
        "verified": True,
        "notes": "한국어 정확 · LlamaForCausalLM · 라이선스 재확인 후 상업 승격 가능",
        "enabled": True,
        "sort_order": 20,
    },
    {
        "hf_id": "LGAI-EXAONE/EXAONE-3.5-2.4B-Instruct",
        "display_name": "EXAONE 3.5 2.4B Instruct",
        "params": "2.4B",
        "license": "EXAONE AI Model License (research-only)",
        "commercial_use": False,
        "verified": True,
        # convert 패치 필요 — scripts/patches/convert_hf_to_gguf_exaone.patch
        # 참고. make setup-distill-toolchain 으로 자동 적용됨.
        "notes": "한국어 특화 · research-only · 상업 배포 전 LG 계약 · convert 패치 필요",
        "enabled": True,
        "sort_order": 30,
    },
    {
        "hf_id": "naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-1.5B",
        "display_name": "Naver HyperCLOVA X SEED Instruct 1.5B",
        "params": "1.5B",
        "license": "HyperCLOVA X SEED (제한적)",
        "commercial_use": False,
        "verified": True,
        "notes": "한국어 특화 · 엣지 최적 (1.0GB, 18-34 tok/s) · 라이선스 제한적",
        "enabled": True,
        "sort_order": 40,
    },
    # 제거된 후보 (2026-04-16 dry-run 기준):
    # - Qwen/Qwen3-4B: 파이프라인 OK 이지만 기본 thinking 모드 + 영어 답변 +
    #   한국사 날짜 오답 일관. 한국어 도메인 사용 불가 → 제거.
]


async def seed_base_models(repo: DistillRepository) -> dict[str, int]:
    """기본 베이스 모델 레지스트리 rows 를 insert-if-missing 방식으로 삽입.

    이미 존재하는 hf_id 는 건드리지 않음 — admin 편집 보존.

    Returns:
        {"inserted": N, "skipped": M} — 신규 삽입 수 / 이미 있어서 스킵된 수.
    """
    inserted = 0
    skipped = 0
    for row in DEFAULT_BASE_MODELS:
        try:
            created = await repo.insert_base_model_if_missing(row)
            if created:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to seed base model %s: %s", row["hf_id"], e)
    logger.info(
        "Seed base models: %d inserted, %d already present (preserved admin edits)",
        inserted, skipped,
    )
    return {"inserted": inserted, "skipped": skipped}
