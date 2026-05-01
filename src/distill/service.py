# pyright: reportMissingImports=false, reportAttributeAccessIssue=false
"""Distill 파이프라인 오케스트레이터.

데이터 생성 → 학습 → 평가 → 양자화 → 배포를 subprocess로 격리 실행.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.distill.config import DistillConfig, DistillProfile
from src.distill.repository import DistillRepository
from src.config.weights import weights as _w

logger = logging.getLogger(__name__)

_re_num_pattern = re.compile(r"^\d+[개월주년일편점]")

# GPU 원격 학습 완료 마커 — SSOT 는 build_executor. 양쪽이 비교에 사용.
from src.distill.build_executor import _GPU_TRAINED  # noqa: E402


def _prefer_reformatted(rows: list[dict]) -> list[dict]:
    """Reformatter / Augmenter 산출물 우선 적용.

    우선순위 (source_type 기준):
      reformatted_aug > reformatted > 원본 (test_seed / usage_log / etc.)

    적용 규칙:
      - 원본 → reformatted 가 있으면 원본 제거, reformatted 사용
      - reformatted_aug 는 reformatted 를 대체하지 않고 **함께** 포함됨
        (Phase 1.5: 같은 fact 의 여러 질문 변형을 모두 exposures 로 사용)

    이 함수는 curated training data export 시 호출돼서, 1B 학습에 유리한 2문단
    포맷 + 질문 증강을 자동으로 쓰도록 한다.
    """
    reformatted = [r for r in rows if r.get("source_type") == "reformatted"]
    reformatted_aug = [r for r in rows if r.get("source_type") == "reformatted_aug"]

    if not reformatted and not reformatted_aug:
        return rows

    # reformatted 의 augmented_from 이 가리키는 원본 id (제거 대상)
    replaced_original_ids = {
        r.get("augmented_from") for r in reformatted if r.get("augmented_from")
    }

    non_reformatted = [
        r for r in rows
        if r.get("source_type") not in ("reformatted", "reformatted_aug")
        and r.get("id") not in replaced_original_ids
    ]
    return non_reformatted + reformatted + reformatted_aug


class DistillService:
    """Distill 파이프라인 오케스트레이터."""

    def __init__(
        self,
        config: DistillConfig,
        session_factory,
        sagemaker_client=None,
        embedder=None,
        qdrant_url: str = "",
    ) -> None:
        from src.config import get_settings
        self.config = config
        self.session_factory = session_factory
        self.llm = sagemaker_client
        self.embedder = embedder
        self.qdrant_url = qdrant_url or get_settings().qdrant.url

    async def generate_data_for_review(self, profile_name: str) -> dict:
        """큐레이션용 QA 데이터 생성 → pending 상태로 DB 저장.

        **PR10 리팩터**: 기존 150줄 인라인 코드가 ``DataGenPipeline`` 으로
        분리됨. 각 단계는 ``src/distill/pipeline/data_gen_stages.py`` 의
        독립 stage 클래스. 이 메서드는 pipeline 을 조립 + 실행 + 저장만 담당.

        Stage 순서: QA 생성 → 범용성 → (legacy aug) → ID 부여 → reformat → augment
        """
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        from src.distill.config import dict_to_profile
        from src.distill.data_gen.generality_filter import GeneralityFilter
        from src.distill.data_generator import DistillDataGenerator
        from src.distill.pipeline.data_gen_stages import (
            AugmentStage,
            GeneralityStage,
            IDAssignStage,
            LegacyAugmentStage,
            QAGenerationStage,
            ReformatStage,
        )
        from src.distill.pipeline.stages import DataGenPipeline, make_context

        repo = DistillRepository(self.session_factory)
        profile_dict = await repo.get_profile(profile_name)
        if not profile_dict:
            raise ValueError(f"Profile not found: {profile_name}")

        search_group = profile_dict.get("search_group", "")
        profile = dict_to_profile(profile_dict)

        generator = DistillDataGenerator(
            self.llm, self.embedder, profile, self.qdrant_url,
        )
        generality = GeneralityFilter(generator.llm_helper)

        group_repo = SearchGroupRepository(self.session_factory)
        kb_ids = await group_repo.resolve_kb_ids(group_name=search_group)
        if not kb_ids:
            raise ValueError(f"Search group '{search_group}' has no KBs")

        # Pipeline 조립
        dq = profile.data_quality
        ctx = make_context(profile_name, profile, kb_ids, search_group)
        pipeline = (
            DataGenPipeline(ctx)
            .add(QAGenerationStage(
                generator, self.session_factory, self.config.defaults.min_training_samples,
            ))
            .add(GeneralityStage(generality))
            .add(LegacyAugmentStage(generator))
            .add(IDAssignStage())
            .add(ReformatStage(generator.llm_helper, concurrency=dq.question_augmenter_concurrency))
            .add(AugmentStage(
                generator.llm_helper,
                n_variations=dq.question_augmenter_count,
                concurrency=dq.question_augmenter_concurrency,
                verify=dq.question_augmenter_verify,
            ))
        )

        # 실행
        result_ctx = await pipeline.run()

        # DB 저장
        rows_to_save = result_ctx.rows + result_ctx.reformatted_rows + result_ctx.augmented_rows
        saved = await repo.save_training_data_batch(rows_to_save)
        logger.info(
            "Generated %d QA pairs for review (batch=%s, profile=%s, "
            "original=%d, reformatted=%d, augmented=%d)",
            saved, result_ctx.batch_id, profile_name,
            len(result_ctx.rows), len(result_ctx.reformatted_rows),
            len(result_ctx.augmented_rows),
        )

        return {
            "batch_id": result_ctx.batch_id,
            "total": saved,
            "usage_log": result_ctx.stage_logs.get("qa_generation", {}).get("usage_log", 0),
            "chunk_qa": result_ctx.stage_logs.get("qa_generation", {}).get("chunk_qa", 0),
            "reformatted": len(result_ctx.reformatted_rows),
            "augmented": len(result_ctx.augmented_rows),
            "stage_logs": result_ctx.stage_logs,
        }

    async def generate_test_data(self, profile_name: str, count: int = 50) -> dict:
        """테스트용 시드 데이터셋 생성 (SageMaker EXAONE Teacher)."""
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        from src.distill.data_gen.generality_filter import GeneralityFilter
        from src.distill.data_gen.test_data_templates import generate_test_qa

        repo = DistillRepository(self.session_factory)
        # DB에서 프로필 조회 (YAML이 아닌 DB 기준 — 대시보드에서 수정된 값 반영)
        profile_dict = await repo.get_profile(profile_name)
        if not profile_dict:
            raise ValueError(f"Profile not found: {profile_name}")
        search_group = profile_dict.get("search_group", "")

        batch_id = str(uuid.uuid4())

        # KB IDs (DB 프로필의 search_group 사용)
        group_repo = SearchGroupRepository(self.session_factory)
        kb_ids = await group_repo.resolve_kb_ids(group_name=search_group)
        if not kb_ids:
            raise ValueError(f"Search group '{search_group}' has no KBs")

        from src.config import get_settings
        rag_url = get_settings().distill.rag_api_url
        logger.info(
            "generate_test_data: llm=%s, kb_ids=%s, search_group=%s",
            type(self.llm).__name__ if self.llm else "None",
            kb_ids, search_group,
        )

        # QualityFilter 생성 (추론 제거 + 답변 정규화용)
        from src.distill.config import dict_to_profile
        from src.distill.data_gen.llm_helper import LLMHelper
        from src.distill.data_gen.quality_filter import QualityFilter
        profile = dict_to_profile(profile_dict)
        llm_helper = LLMHelper(self.llm, self.qdrant_url, concurrency=3, timeout_sec=60)
        qf = QualityFilter(llm_helper, self.embedder, profile)

        # 기존 질문 가져오기 (중복 방지)
        existing_result = await repo.list_training_data(
            profile_name=profile_name, limit=10000,
        )
        existing_questions = {
            it["question"] for it in existing_result.get("items", [])
        }
        logger.info("Existing questions for dedup: %d", len(existing_questions))

        test_qa = await generate_test_qa(
            llm_client=self.llm,
            qdrant_url=self.qdrant_url,
            kb_ids=kb_ids,
            count=count,
            rag_api_url=rag_url,
            quality_filter=qf,
            existing_questions=existing_questions,
        )

        # 범용성 점수
        generality = GeneralityFilter()
        test_qa = await generality.batch_score(test_qa)

        # pending으로 저장
        for qa in test_qa:
            qa["id"] = str(uuid.uuid4())
            qa["profile_name"] = profile_name
            qa["status"] = "pending"
            qa["source_type"] = "test_seed"
            qa["generation_batch_id"] = batch_id

        saved = await repo.save_training_data_batch(test_qa)
        return {"batch_id": batch_id, "total": saved}

    async def augment_approved_data(
        self, profile_name: str, max_variants: int = 3,
    ) -> dict:
        """승인된 QA를 질문 변형으로 증강."""
        from src.distill.data_gen.dataset_builder import DatasetBuilder
        from src.distill.data_gen.llm_helper import LLMHelper

        repo = DistillRepository(self.session_factory)
        # test_seed만 augmentation (용어 QA는 정의 질문이라 변형 불필요)
        result = await repo.list_training_data(
            profile_name=profile_name, status="approved",
            source_type="test_seed", limit=10000,
        )
        approved = result.get("items", [])
        if not approved:
            raise ValueError("No approved test_seed data to augment")

        batch_id = str(uuid.uuid4())

        # LLM helper로 질문 변형 생성
        profile_dict = await repo.get_profile(profile_name)
        from src.distill.config import dict_to_profile
        profile = dict_to_profile(profile_dict) if profile_dict else None

        llm_helper = LLMHelper(self.llm, self.qdrant_url, concurrency=3, timeout_sec=60)
        builder = DatasetBuilder(llm_helper, profile)

        # ID 할당 (augmented_from 추적용)
        for qa in approved:
            if not qa.get("id"):
                qa["id"] = str(uuid.uuid4())

        # 질문 변형 생성
        profile.data_quality.augmentation_count = max_variants
        augmented = await builder.augment_questions(approved)
        new_variants = [q for q in augmented if q.get("augmented_from")]

        # Hub Search로 변형 질문 답변 검증
        import httpx
        from src.config import get_settings
        rag_url = get_settings().distill.rag_api_url
        search_group = (profile_dict or {}).get("search_group", "")

        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        group_repo = SearchGroupRepository(self.session_factory)
        kb_ids = await group_repo.resolve_kb_ids(group_name=search_group)

        verified: list[dict] = []
        async with httpx.AsyncClient(timeout=_w.timeouts.httpx_distill_teacher) as client:
            for variant in new_variants:
                try:
                    resp = await client.post(
                        f"{rag_url}/api/v1/search/hub",
                        json={"query": variant["question"], "kb_ids": kb_ids,
                              "top_k": 3, "include_answer": True},
                    )
                    resp.raise_for_status()
                    sr = resp.json()
                    answer = sr.get("answer", "")
                    confidence = sr.get("confidence", "")

                    if not answer or confidence in ("낮음", "low"):
                        continue

                    variant["answer"] = answer.strip()
                    variant["id"] = str(uuid.uuid4())
                    variant["profile_name"] = profile_name
                    variant["status"] = "pending"
                    variant["generation_batch_id"] = batch_id
                    variant["augmentation_verified"] = True
                    verified.append(variant)
                except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                    logger.warning("Augmentation verify failed: %s", e)

        saved = await repo.save_training_data_batch(verified)
        logger.info(
            "Augmented %d approved → %d variants → %d verified (batch=%s)",
            len(approved), len(new_variants), saved, batch_id,
        )
        return {"batch_id": batch_id, "original": len(approved),
                "variants": len(new_variants), "verified": saved}

    async def generate_term_qa(
        self, profile_name: str, top_n: int = 100,
    ) -> dict:
        """PBU 핵심 용어 → QA 학습 데이터 생성."""
        from sqlalchemy import text

        repo = DistillRepository(self.session_factory)
        profile_dict = await repo.get_profile(profile_name)
        if not profile_dict:
            raise ValueError(f"Profile not found: {profile_name}")

        search_group = profile_dict.get("search_group", "")
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        group_repo = SearchGroupRepository(self.session_factory)
        kb_ids = await group_repo.resolve_kb_ids(group_name=search_group)

        # PBU KB 용어 중 고빈도 용어 추출
        batch_id = str(uuid.uuid4())
        terms: list[dict] = []

        async with self.session_factory() as session:
            # PBU_ 도메인 용어 사용 (GS 데이터 표준 용어)
            result = await session.execute(
                text("""
                    SELECT term, term_ko, definition, kb_id, occurrence_count
                    FROM glossary_terms
                    WHERE kb_id LIKE 'PBU_%%'
                    AND status = 'approved'
                    AND definition IS NOT NULL
                    AND length(definition) > 20
                    ORDER BY occurrence_count DESC
                    LIMIT :limit
                """),
                {"limit": top_n},
            )
            for row in result.fetchall():
                # term_ko(한글 용어) 우선 사용
                display_term = row[1] if row[1] and row[1] != row[0] else row[0]
                terms.append({
                    "term": display_term,
                    "term_physical": row[0],
                    "definition": row[2],
                    "kb_id": row[3], "count": row[4],
                })

        # 일반어 제외 목록 (GS 도메인 특화가 아닌 한국어 일반 명사)
        _COMMON_WORDS = {
            "매출", "상품", "금액", "기간", "납부", "내역", "비교", "분류",
            "문서", "첨부", "사실", "사업", "경쟁", "건수", "단가", "수량",
            "현황", "항목", "내용", "결과", "방법", "절차", "기준", "대상",
            "관련", "확인", "처리", "등록", "변경", "삭제", "조회", "관리",
            "설명", "안내", "요청", "승인", "거부", "완료", "진행", "시작",
            "종료", "이용", "사용", "제공", "포함", "제외", "적용", "해당",
            "필요", "가능", "불가", "정상", "오류", "해지", "명의", "권장",
            "숙지", "검토", "보고", "전달", "수정", "추가", "개선", "분석",
        }

        # Kiwi 형태소 분석기 (1회 초기화)
        _kiwi_instance = None
        try:
            from kiwipiepy import Kiwi
            _kiwi_instance = Kiwi()
        except ImportError:
            pass

        # 품질 필터
        filtered_terms = []
        for t in terms:
            defn = t["definition"]
            term = t["term"]

            # 정의 부실 제외
            bad_patterns = [
                "제공된 문맥에서", "명확한 정의를 도출하기 어렵",
                "직접적인 정의가", "구체적인 정의",
                "명확한 의미를 파악하기 어렵", "정확한 의미를 파악하기 어렵",
                "정확한 정의를 내리기 어렵", "명확한 정의를 제공하지 않",
                "운영DB 미등록", "DA작업", "표준으로 변경",
            ]
            if any(p in defn for p in bad_patterns):
                continue
            if len(defn) < 20:
                continue

            # 일반어 제외
            if term in _COMMON_WORDS:
                continue

            # 2글자 이하 한글 일반 명사 제외
            if len(term) <= 2 and all("\uac00" <= c <= "\ud7a3" for c in term):
                continue

            # 숫자/기간 패턴 제외 (1개월전, 12개월, 13주 등)
            if _re_num_pattern.match(term):
                continue

            # 1~2글자 영문 약어 제외 (너무 짧아서 의미 없음)
            if len(term) <= 2 and term.isascii():
                continue

            # Kiwi 형태소 분석으로 일반어 자동 필터
            # NNG(일반명사)만으로 구성 + 비즈니스 맥락 없음 → 일반어
            if _kiwi_instance:
                tokens = _kiwi_instance.tokenize(term)
                noun_tags = [t.tag for t in tokens if t.tag.startswith("NN")]
                is_all_nng = len(noun_tags) > 0 and all(t == "NNG" for t in noun_tags)

                if is_all_nng:
                    # 비즈니스/GS 맥락 키워드가 정의에 있으면 보존
                    biz_keywords = [
                        "가맹", "점포", "매출", "정산", "계약", "본부", "POS",
                        "발주", "경영", "수수료", "세금", "회계", "장부", "재고",
                        "시스템", "GS", "편의점", "배분", "손익", "임대", "보상",
                    ]
                    has_biz = any(kw in defn for kw in biz_keywords)
                    if not has_biz:
                        continue  # 일반어 제외

            # 매장명/사람명 제외
            if term.endswith("점") and len(term) >= 3:
                continue
            if term.endswith(("님", "씨", "M")):
                continue
            # 한글 3자 사람 이름 제외 (대부분 성+이름)
            if len(term) == 3 and all("\uac00" <= c <= "\ud7a3" for c in term):
                # 용어 정의에 사람 관련 키워드가 있으면 사람 이름으로 판단
                person_keywords = ["운영", "담당", "매니저", "점장", "점주", "매장",
                                   "M이", "SC", "지점", "관리하", "수행"]
                if any(w in defn[:150] for w in person_keywords):
                    continue
                # 한글 3자 + category가 noun이고 definition에 "~는 ~인"이 아닌 것도
                # 사람 이름일 가능성 높음 (보수적 필터)
                if t.get("count", 0) < 50:
                    continue  # 빈도 낮은 3글자는 사람 이름 가능성 높음
            # 일반 식품/물품명 제외
            if term in {"아이스크림", "맥주", "음료", "과자", "라면", "담배",
                        "우유", "빵", "김밥", "도시락", "커피", "물"}:
                continue

            filtered_terms.append(t)

        # 동일 용어(term_ko 기준) 중복 제거 — 첫 번째만 유지
        seen_terms: set[str] = set()
        unique_terms: list[dict] = []
        for t in filtered_terms:
            if t["term"] not in seen_terms:
                seen_terms.add(t["term"])
                unique_terms.append(t)
        terms = unique_terms

        if not terms:
            raise ValueError(f"No quality terms found for KBs: {kb_ids}")

        # 용어 → QA 변환
        qa_pairs: list[dict] = []
        for t in terms:
            term = t["term"]
            # 한글 종성 판별 → 자연스러운 조사
            last_char = term[-1] if term else ""
            has_jongseong = last_char >= "\uac00" and (ord(last_char) - 0xAC00) % 28 != 0
            subj = f"{term}이" if has_jongseong else f"{term}가"
            physical = t.get("term_physical", "")
            questions = [
                f"{subj} 뭐야?",
                f"{term}에 대해 설명해줘",
            ]
            # 물리명(약어)과 한글명이 다르면 약어 질문도 추가
            if physical and physical != term and physical.upper() == physical:
                questions.append(f"{physical}가 뭐야?")
            for q in questions:
                qa_pairs.append({
                    "id": str(uuid.uuid4()),
                    "profile_name": profile_name,
                    "question": q,
                    "answer": t["definition"],
                    "source_type": "term_qa",
                    "source_id": f"glossary_{t['kb_id']}",
                    "kb_id": t["kb_id"],
                    "status": "pending",
                    "generation_batch_id": batch_id,
                    "generality_score": 1.0,  # 용어는 범용적
                })

        saved = await repo.save_training_data_batch(qa_pairs)
        logger.info("Generated %d term QA pairs from %d terms (batch=%s)",
                     saved, len(terms), batch_id)
        return {"batch_id": batch_id, "terms": len(terms), "qa_pairs": saved}

    async def run_pipeline(
        self,
        build_id: str,
        profile_name: str,
        steps: list[str] | None = None,
        use_curated_data: bool = False,
    ) -> None:
        """전체 파이프라인 실행 (sync wrapper — local 모드 전용 또는 backward compat).

        GPU 모드에서는 ``run_pipeline_pre_train`` 가 awaiting_gpu 반환 후 종료
        — sweeper 가 ``run_pipeline_post_train`` 호출. 본 wrapper 는 단일 코드
        경로로 양쪽 케이스 모두 처리.
        """
        from src.distill.build_executor import BuildPipelineExecutor
        repo = DistillRepository(self.session_factory)
        executor = BuildPipelineExecutor(
            self, repo, build_id, profile_name,
            use_curated_data=use_curated_data,
        )
        await executor.run(steps=steps)

    async def run_pipeline_pre_train(
        self,
        build_id: str,
        profile_name: str,
        steps: list[str] | None = None,
        use_curated_data: bool = False,
    ) -> dict:
        """generate + train 시작까지 실행. arq job 진입점.

        Returns:
            {"phase": "awaiting_gpu", "build_id": ...} — GPU 학습 시작, sweeper
                가 이어받음.
            {"phase": "post_train_ready", "train_result": {...}} — local mode
                또는 train step 없음 → 즉시 post_train 실행 가능.
        """
        from src.distill.build_executor import BuildPipelineExecutor
        repo = DistillRepository(self.session_factory)
        executor = BuildPipelineExecutor(
            self, repo, build_id, profile_name,
            use_curated_data=use_curated_data,
        )
        return await executor.run_pre_train(steps=steps)

    async def run_pipeline_post_train(
        self,
        build_id: str,
        profile_name: str,
        train_result: dict,
        steps: list[str] | None = None,
    ) -> None:
        """evaluate + quantize + deploy. sweeper 또는 pre_train 가 호출.

        train_result:
            {"gpu_trained": True, "result_json": {...}} — GPU 학습 결과
            {"gpu_trained": False, "model_path": "..."} — local 학습 결과
            {"gpu_trained": False, "model_path": None} — train step 없음 (reset-to-base)
        """
        from src.distill.build_executor import BuildPipelineExecutor
        repo = DistillRepository(self.session_factory)
        executor = BuildPipelineExecutor(
            self, repo, build_id, profile_name,
        )
        await executor.run_post_train(train_result, steps=steps)

    async def _generate_data(
        self, build_id: str, profile_name: str, profile: DistillProfile,
        repo: DistillRepository, build_dir: Path,
        *, use_curated_data: bool = False,
    ) -> str:
        """QA 데이터 생성.

        use_curated_data=True: DB에서 approved 데이터만 export (큐레이션 경로)
        use_curated_data=False: 자동 생성 + auto-approve (기존 경로)
        """
        # 최소 학습 데이터 수 (파일럿: 200, 운영: distill.yaml에서 설정)
        min_samples = 200

        # ── 큐레이션 경로: DB에서 approved 데이터 export ──
        if use_curated_data:
            result = await repo.list_training_data(
                profile_name=profile_name, status="approved", limit=100000,
            )
            approved = result.get("items", [])
            if not approved:
                raise ValueError("No approved training data. Run data curation first.")

            # Reformatter 경로 우선 적용 — source_type="reformatted" 행이 있으면
            # 동일 원본(augmented_from) 의 원본 행을 제거하고 reformatted 로 대체.
            # 이렇게 하면 기존 긴 RAG 답변 대신 1B 가 학습 가능한 2문단 포맷이 들어감.
            # reformatted 가 아직 없는 샘플은 원본 그대로 유지 (점진 전환).
            approved = _prefer_reformatted(approved)

            data_path = str(build_dir / "train.jsonl")
            from src.distill.data_gen.dataset_builder import DatasetBuilder
            count = DatasetBuilder.export_jsonl(approved, data_path)

            reformatted_count = sum(
                1 for q in approved if q.get("source_type") == "reformatted"
            )
            data_sources = {
                "approved": count,
                "reformatted": reformatted_count,
                "original": count - reformatted_count,
                "source": "curated",
            }
            await repo.update_build(
                build_id, training_samples=count,
                data_sources=json.dumps(data_sources, ensure_ascii=False),
            )
            if count < min_samples:
                raise ValueError(f"Insufficient approved data: {count} < {min_samples}")
            return data_path

        # ── 기존 경로: 자동 생성 + auto-approve ──
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        from src.distill.data_generator import DistillDataGenerator

        generator = DistillDataGenerator(
            self.llm, self.embedder, profile, self.qdrant_url,
        )

        group_repo = SearchGroupRepository(self.session_factory)
        kb_ids = await group_repo.resolve_kb_ids(group_name=profile.search_group)
        if not kb_ids:
            raise ValueError(f"Search group '{profile.search_group}' has no KBs")

        log_qa = await generator.generate_from_usage_logs(
            self.session_factory, kb_ids, profile.search_group,
        )
        logger.info("Main source (usage_log): %d high-quality QA pairs", len(log_qa))

        # ── 보조 소스: 청크 기반 QA 생성 (로그 부족 시) ──
        chunk_qa: list[dict] = []
        if len(log_qa) < min_samples:
            shortage = min_samples - len(log_qa)
            logger.info(
                "Usage log insufficient (%d < %d), generating %d chunk QA pairs",
                len(log_qa), min_samples, shortage,
            )
            chunk_qa = await generator.generate_from_chunks(
                kb_ids, max_chunks_per_kb=max(shortage // len(kb_ids), 50),
            )

        # ── 재학습 데이터 (DB에서) ──
        retrain_result = await repo.list_training_data(
            profile_name=profile_name, source_type="retrain", limit=5000,
        )
        retrain_qa = retrain_result.get("items", [])

        # 병합 (메인 → 보조 → 재학습 순서로 우선)
        all_qa = await generator.merge_and_deduplicate(log_qa, chunk_qa, retrain_qa)

        # Augmentation (다양한 표현으로 질문 증강)
        all_qa = await generator.augment_questions(all_qa)

        # 밸런싱
        all_qa = generator.balance_dataset(all_qa)

        # JSONL 저장
        data_path = str(build_dir / "train.jsonl")
        count = generator.export_jsonl(all_qa, data_path)

        # 데이터 통계 업데이트
        data_sources = {
            "chunk_qa": len(chunk_qa),
            "usage_log": len(log_qa),
            "retrain": len(retrain_qa),
            "total_after_dedup": count,
        }
        await repo.update_build(
            build_id,
            training_samples=count,
            data_sources=json.dumps(data_sources, ensure_ascii=False),
        )

        # 최소 데이터 수 확인
        if count < min_samples:
            raise ValueError(f"Insufficient data: {count} < {min_samples}")

        return data_path

    async def _train(
        self, build_id: str, profile: DistillProfile,
        data_path: str, repo: DistillRepository, build_dir: Path,
    ) -> str:
        """LoRA SFT 학습 (GPU EC2 우선, 없으면 로컬).

        GPU 경로: ``start_gpu_training`` 가 EC2 부팅만 트리거 + 즉시 return.
        실제 완료 detection 은 arq sweeper (``distill_sweep_training``) 가 60s
        주기로 S3 result.json 확인 → ``distill_pipeline_post_train`` enqueue.
        train_loss / duration_sec / gguf_size_mb 등 메트릭은 sweeper 가 sweep
        성공 시 result.json 에서 읽어 update_build 호출.

        ``_GPU_TRAINED`` 반환은 executor.run_pre_train 이 "awaiting_gpu" 상태로
        해석 — pipeline 정지 + sweeper 위임.
        """
        import os

        gpu_instance = os.getenv("DISTILL_GPU_INSTANCE_ID", "")

        if gpu_instance:
            logger.info("Starting GPU EC2 training (sweeper takes over): %s", gpu_instance)
            from src.distill.gpu_trainer import start_gpu_training

            started = await start_gpu_training(
                build_id=build_id,
                jsonl_path=data_path,
                config={
                    "base_model": profile.base_model,
                    "lora": {"r": profile.lora.r, "alpha": profile.lora.alpha,
                             "dropout": profile.lora.dropout,
                             "target_modules": profile.lora.target_modules},
                    "training": {"epochs": profile.training.epochs,
                                 "batch_size": profile.training.batch_size,
                                 "gradient_accumulation": profile.training.gradient_accumulation,
                                 "learning_rate": profile.training.learning_rate,
                                 "max_seq_length": profile.training.max_seq_length},
                    "quantize": profile.deploy.quantize or "q4_k_m",
                },
                s3_bucket=profile.deploy.s3_bucket,
                s3_prefix=profile.deploy.s3_prefix,
            )

            if started.get("status") != "started":
                raise RuntimeError(f"GPU start failed: {started.get('error', 'unknown')}")

            # 신구조 marker — gpu_instance_id NOT NULL 이라야 sweeper 가 본 build 를 잡음.
            await repo.update_build(
                build_id,
                gpu_instance_id=started["gpu_instance_id"],
                s3_result_key=started["s3_result_key"],
                gpu_started_at=started["started_at"],
            )

            # GPU 경로 — 즉시 return. executor 가 _GPU_TRAINED 로 awaiting_gpu 로 해석.
            return _GPU_TRAINED

        # 로컬 학습 (fallback)
        logger.info("Using local CPU/MPS for training (no GPU instance configured)")
        from src.distill.trainer import DistillTrainer

        trainer = DistillTrainer(profile, output_dir=str(build_dir / "model"))
        dataset = await asyncio.to_thread(trainer.prepare_dataset, data_path)
        result = await asyncio.to_thread(trainer.train, dataset)

        await repo.update_build(
            build_id,
            train_loss=result.training_loss,
            eval_loss=result.eval_loss,
            training_duration_sec=result.duration_sec,
        )

        model_path = str(build_dir / "model" / "merged")
        await asyncio.to_thread(trainer.merge_and_save, model_path)
        return model_path

    async def _evaluate(
        self, build_id: str, profile: DistillProfile,
        model_path: str, data_path: str, repo: DistillRepository,
        *, gguf_path: str | None = None,
    ) -> bool:
        """모델 평가 + 배포 게이트 — fail-closed.

        데이터 부재 / 평가 실패 / GGUF 미적재 모두 fail-closed (return False).
        ``build.force_deploy=True`` 일 때만 우회 (경고 로그).

        train_loss<2.0 fallback 제거 — Reformatter 적용 후 손실 분포가
        다르므로 의미 없음. 평가 실패는 명시적으로 fail.
        """
        build = await repo.get_build(build_id) or {}
        force_deploy = bool(build.get("force_deploy", False))

        def _gate_fail(reason: str) -> bool:
            if force_deploy:
                logger.warning("Eval gate FAIL — force_deploy=True 로 우회: %s", reason)
                return True
            logger.error("Eval gate FAIL (fail-closed): %s", reason)
            return False

        # 1. data 파일 부재 → fail-closed
        if not Path(data_path).exists():
            return _gate_fail(f"eval data file not found: {data_path}")

        # 2. eval set 로드 (train.jsonl 마지막 10%)
        with open(data_path, encoding="utf-8") as f:
            lines = f.readlines()
        eval_lines = lines[int(len(lines) * 0.9):]
        eval_data: list[dict] = []
        for line in eval_lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            msgs = entry.get("messages", [])
            if len(msgs) >= 2:
                eval_data.append({
                    "question": msgs[0]["content"],
                    "answer": msgs[1]["content"],
                })

        if not eval_data:
            return _gate_fail("eval_data is empty after parsing train.jsonl")

        # 3. GGUF 평가 prerequisites 검증 → fail-closed
        if not (gguf_path and self.llm and self.embedder):
            return _gate_fail(
                f"GGUF eval prerequisites missing: "
                f"gguf={bool(gguf_path)} llm={bool(self.llm)} embedder={bool(self.embedder)}"
            )

        # 4. GGUF 평가 실행
        try:
            from src.distill.evaluator import DistillEvaluator

            evaluator = DistillEvaluator(self.llm, self.embedder)
            threshold = getattr(profile.training, "eval_threshold", None)
            result = await evaluator.evaluate(gguf_path, eval_data, threshold)
        except ImportError as e:
            return _gate_fail(f"llama_cpp not available: {e}")
        except (RuntimeError, OSError, ValueError) as e:
            return _gate_fail(f"GGUF evaluation raised: {e}")

        await repo.update_build(
            build_id,
            eval_passed=result.passed,
            eval_faithfulness=result.faithfulness,
            eval_relevancy=result.relevancy,
        )
        logger.info(
            "GGUF evaluation: passed=%s, faithfulness=%.3f, relevancy=%.3f",
            result.passed, result.faithfulness, result.relevancy,
        )

        if not result.passed:
            return _gate_fail(
                f"eval failed: faithfulness={result.faithfulness:.3f} "
                f"relevancy={result.relevancy:.3f}"
            )
        return True

    async def _download_gguf_from_s3(
        self, build_id: str, profile: DistillProfile, build_dir: Path,
    ) -> str | None:
        """GPU 학습 후 S3에서 GGUF 다운로드 (평가용)."""
        from src.distill.deployer import _s3_client
        s3_key = f"{profile.deploy.s3_prefix}train/{build_id}/output/model.gguf"
        local_path = str(build_dir / "model.gguf")

        try:
            s3 = _s3_client()
            s3.download_file(profile.deploy.s3_bucket, s3_key, local_path)
            logger.info("Downloaded GGUF from S3: %s", s3_key)
            return local_path
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("GGUF download failed (eval will use train_loss fallback): %s", e)
            return None

    async def _quantize(
        self, build_id: str, profile: DistillProfile,
        model_path: str, repo: DistillRepository, build_dir: Path,
    ) -> str:
        """GGUF 양자화."""
        from src.distill.quantizer import DistillQuantizer

        quantizer = DistillQuantizer(profile)
        gguf_path = str(build_dir / "model.gguf")
        await asyncio.to_thread(quantizer.quantize_to_gguf, model_path, gguf_path)

        # 검증
        validation = await asyncio.to_thread(quantizer.validate_gguf, gguf_path)
        if not validation.get("valid"):
            raise RuntimeError(f"GGUF validation failed: {validation.get('error')}")

        await repo.update_build(
            build_id,
            gguf_size_mb=validation.get("size_mb", 0),
            gguf_sha256=validation.get("sha256", ""),
            quantize_method=profile.deploy.quantize,
            model_name=profile.base_model.split("/")[-1] if profile.base_model else "",
        )

        return gguf_path

    async def _deploy(
        self, build_id: str, profile: DistillProfile,
        gguf_path: str, repo: DistillRepository,
        gpu_trained: bool = False,
    ) -> None:
        """S3 배포.

        - 로컬 학습: 로컬 GGUF를 `{prefix}{version}/model.gguf` 로 업로드.
        - GPU 학습: 이미 S3 훈련 출력 경로에 존재하므로 `copy_in_s3` 로 버전 경로에 복사.
        """
        from src.distill.deployer import DistillDeployer

        deployer = DistillDeployer(profile)
        build = await repo.get_build(build_id)
        version = build["version"]
        profile_name = build["profile_name"]

        if gpu_trained:
            src_uri = build.get("s3_uri")
            if not src_uri:
                raise RuntimeError(
                    f"GPU-trained build {build_id} has no s3_uri — cannot deploy",
                )
            s3_uri = await deployer.copy_in_s3(src_uri, version)
        else:
            s3_uri = await deployer.upload_to_s3(gguf_path, version)

        await deployer.create_and_upload_manifest(s3_uri, version, build)

        # s3_uri 만 update — deployed_at 은 mark_build_deployed 가 처리하여
        # 같은 profile 의 이전 deployed 빌드를 NULL 로 정리해 'active 1개' 불변식 유지.
        await repo.update_build(build_id, s3_uri=s3_uri)
        await repo.mark_build_deployed(build_id, profile_name)
