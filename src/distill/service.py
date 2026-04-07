"""Distill 파이프라인 오케스트레이터.

데이터 생성 → 학습 → 평가 → 양자화 → 배포를 subprocess로 격리 실행.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.distill.config import DistillConfig, DistillProfile
from src.distill.repository import DistillRepository

logger = logging.getLogger(__name__)


class DistillService:
    """Distill 파이프라인 오케스트레이터."""

    def __init__(
        self,
        config: DistillConfig,
        session_factory,
        sagemaker_client=None,
        embedder=None,
        qdrant_url: str = "http://localhost:6333",
    ):
        self.config = config
        self.session_factory = session_factory
        self.llm = sagemaker_client
        self.embedder = embedder
        self.qdrant_url = qdrant_url

    async def generate_data_for_review(self, profile_name: str) -> dict:
        """큐레이션용 QA 데이터 생성 → pending 상태로 DB 저장."""
        from src.database.repositories.search_group import SearchGroupRepository
        from src.distill.data_gen.generality_filter import GeneralityFilter
        from src.distill.data_generator import DistillDataGenerator

        repo = DistillRepository(self.session_factory)
        # DB에서 프로필 조회 (YAML 아닌 DB 기준 — 대시보드 수정 반영)
        profile_dict = await repo.get_profile(profile_name)
        if not profile_dict:
            raise ValueError(f"Profile not found: {profile_name}")

        search_group = profile_dict.get("search_group", "")
        from src.distill.config import dict_to_profile
        profile = dict_to_profile(profile_dict)

        batch_id = str(uuid.uuid4())
        generator = DistillDataGenerator(
            self.llm, self.embedder, profile, self.qdrant_url,
        )
        generality = GeneralityFilter(
            generator.llm_helper if hasattr(generator, "llm_helper") else None
        )

        # KB IDs 확보 (DB 프로필의 search_group 사용)
        group_repo = SearchGroupRepository(self.session_factory)
        kb_ids = await group_repo.resolve_kb_ids(group_name=search_group)
        if not kb_ids:
            raise ValueError(f"Search group '{search_group}' has no KBs")

        # QA 생성
        log_qa = await generator.generate_from_usage_logs(
            self.session_factory, kb_ids, search_group,
        )
        chunk_qa: list[dict] = []
        if len(log_qa) < self.config.defaults.min_training_samples:
            chunk_qa = await generator.generate_from_chunks(
                kb_ids, max_chunks_per_kb=50,
            )

        all_qa = await generator.merge_and_deduplicate(log_qa, chunk_qa)

        # 범용성 점수 부여
        all_qa = await generality.batch_score(all_qa)

        # Augmentation + 검증
        all_qa = await generator.augment_questions(all_qa)
        if hasattr(generator, "dataset_builder") and hasattr(generator, "quality_filter"):
            all_qa = await generator.dataset_builder.verify_augmented_questions(
                all_qa, generator.quality_filter,
            )

        # pending으로 DB 저장
        for qa in all_qa:
            qa["id"] = str(uuid.uuid4())
            qa["profile_name"] = profile_name
            qa["status"] = "pending"
            qa["generation_batch_id"] = batch_id

        saved = await repo.save_training_data_batch(all_qa)
        logger.info(
            "Generated %d QA pairs for review (batch=%s, profile=%s)",
            saved, batch_id, profile_name,
        )

        return {
            "batch_id": batch_id,
            "total": saved,
            "usage_log": len(log_qa),
            "chunk_qa": len(chunk_qa),
        }

    async def generate_test_data(self, profile_name: str, count: int = 50) -> dict:
        """테스트용 시드 데이터셋 생성 (SageMaker EXAONE Teacher)."""
        from src.database.repositories.search_group import SearchGroupRepository
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

        test_qa = await generate_test_qa(
            llm_client=self.llm,
            qdrant_url=self.qdrant_url,
            kb_ids=kb_ids,
            count=count,
            rag_api_url=rag_url,
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
        result = await repo.list_training_data(
            profile_name=profile_name, status="approved", limit=10000,
        )
        approved = result.get("items", [])
        if not approved:
            raise ValueError("No approved data to augment")

        batch_id = str(uuid.uuid4())

        # LLM helper로 질문 변형 생성
        profile_dict = await repo.get_profile(profile_name)
        from src.distill.config import dict_to_profile
        profile = dict_to_profile(profile_dict) if profile_dict else None

        llm_helper = LLMHelper(self.llm, concurrency=3, timeout=60)
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

        from src.database.repositories.search_group import SearchGroupRepository
        group_repo = SearchGroupRepository(self.session_factory)
        kb_ids = await group_repo.resolve_kb_ids(group_name=search_group)

        verified: list[dict] = []
        async with httpx.AsyncClient(timeout=60) as client:
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
                except Exception as e:
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
        from src.database.repositories.search_group import SearchGroupRepository
        group_repo = SearchGroupRepository(self.session_factory)
        kb_ids = await group_repo.resolve_kb_ids(group_name=search_group)

        # PBU KB 용어 중 고빈도 용어 추출
        batch_id = str(uuid.uuid4())
        terms: list[dict] = []

        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT term, definition, kb_id, occurrence_count
                    FROM glossary_terms
                    WHERE kb_id = ANY(:kb_ids)
                    AND status = 'approved'
                    AND definition IS NOT NULL
                    AND length(definition) > 20
                    ORDER BY occurrence_count DESC
                    LIMIT :limit
                """),
                {"kb_ids": kb_ids, "limit": top_n},
            )
            for row in result.fetchall():
                terms.append({
                    "term": row[0], "definition": row[1],
                    "kb_id": row[2], "count": row[3],
                })

        if not terms:
            raise ValueError(f"No terms found for KBs: {kb_ids}")

        # 용어 → QA 변환
        qa_pairs: list[dict] = []
        for t in terms:
            # 다양한 질문 형태
            questions = [
                f"{t['term']}이(가) 뭐야?",
                f"{t['term']}에 대해 설명해줘",
            ]
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
        """전체 파이프라인 실행 (별도 프로세스 또는 in-process)."""
        repo = DistillRepository(self.session_factory)
        profile_dict = await repo.get_profile(profile_name)
        if not profile_dict:
            await repo.update_build(build_id, status="failed",
                                    error_message=f"Profile not found: {profile_name}")
            return
        from src.distill.config import dict_to_profile
        profile = dict_to_profile(profile_dict)

        all_steps = steps or ["generate", "train", "evaluate", "quantize", "deploy"]
        from src.config import get_settings
        work_dir = Path(get_settings().distill.work_dir)
        build_dir = work_dir / build_id
        build_dir.mkdir(parents=True, exist_ok=True)

        try:
            data_path = str(build_dir / "train.jsonl")
            model_path = str(build_dir / "model" / "merged")
            gguf_path = str(build_dir / "model.gguf")

            # Step 1: 데이터 생성
            if "generate" in all_steps:
                await repo.update_build(build_id, status="generating")
                data_path = await self._generate_data(
                    build_id, profile_name, profile, repo, build_dir,
                    use_curated_data=use_curated_data,
                )

            # Step 2: 학습
            if "train" in all_steps:
                await repo.update_build(build_id, status="training")
                model_path = await self._train(build_id, profile, data_path, repo, build_dir)

            # Step 3: 평가
            if "evaluate" in all_steps:
                await repo.update_build(build_id, status="evaluating")
                passed = await self._evaluate(build_id, profile, model_path, data_path, repo)
                if not passed:
                    await repo.update_build(
                        build_id, status="failed",
                        error_message="Evaluation below threshold",
                        error_step="evaluate",
                    )
                    return

            # Step 4: 양자화
            if "quantize" in all_steps:
                await repo.update_build(build_id, status="quantizing")
                gguf_path = await self._quantize(build_id, profile, model_path, repo, build_dir)

            # Step 5: 배포
            if "deploy" in all_steps:
                await repo.update_build(build_id, status="deploying")
                await self._deploy(build_id, profile, gguf_path, repo)

            await repo.update_build(build_id, status="completed")
            logger.info("Build %s completed successfully", build_id)

        except Exception as e:
            logger.error("Build %s failed: %s", build_id, e)
            await repo.update_build(
                build_id, status="failed",
                error_message=str(e)[:1000],
            )
        finally:
            # /tmp 정리
            if build_dir.exists():
                shutil.rmtree(build_dir, ignore_errors=True)

    async def _generate_data(
        self, build_id: str, profile_name: str, profile: DistillProfile,
        repo: DistillRepository, build_dir: Path,
        *, use_curated_data: bool = False,
    ) -> str:
        """QA 데이터 생성.

        use_curated_data=True: DB에서 approved 데이터만 export (큐레이션 경로)
        use_curated_data=False: 자동 생성 + auto-approve (기존 경로)
        """
        min_samples = self.config.defaults.min_training_samples

        # ── 큐레이션 경로: DB에서 approved 데이터 export ──
        if use_curated_data:
            result = await repo.list_training_data(
                profile_name=profile_name, status="approved", limit=100000,
            )
            approved = result.get("items", [])
            if not approved:
                raise ValueError("No approved training data. Run data curation first.")

            data_path = str(build_dir / "train.jsonl")
            from src.distill.data_gen.dataset_builder import DatasetBuilder
            count = DatasetBuilder.export_jsonl(approved, data_path)

            data_sources = {"approved": count, "source": "curated"}
            await repo.update_build(
                build_id, training_samples=count,
                data_sources=json.dumps(data_sources, ensure_ascii=False),
            )
            if count < min_samples:
                raise ValueError(f"Insufficient approved data: {count} < {min_samples}")
            return data_path

        # ── 기존 경로: 자동 생성 + auto-approve ──
        from src.database.repositories.search_group import SearchGroupRepository
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
        """LoRA SFT 학습."""
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
    ) -> bool:
        """모델 평가 + 배포 게이트."""
        # from src.distill.evaluator import DistillEvaluator  # TODO: 실 평가 시 활성화

        # eval set 로드 (train.jsonl에서 마지막 10% 사용)
        eval_data = []
        with open(data_path, encoding="utf-8") as f:
            lines = f.readlines()
        eval_lines = lines[int(len(lines) * 0.9):]
        for line in eval_lines:
            entry = json.loads(line)
            msgs = entry.get("messages", [])
            if len(msgs) >= 2:
                eval_data.append({
                    "question": msgs[0]["content"],
                    "answer": msgs[1]["content"],
                })

        if not eval_data:
            logger.warning("No eval data, skipping evaluation")
            return True

        # TODO: GGUF 변환 후 DistillEvaluator로 실 평가 (현재는 loss 기반 게이트)
        # evaluator = DistillEvaluator(self.llm, self.embedder)
        # result = await evaluator.evaluate(gguf_path, eval_data, threshold)
        build = await repo.get_build(build_id)
        train_loss = build.get("train_loss", 999)

        # 간단한 게이트: train_loss < 2.0이면 통과
        passed = train_loss < 2.0
        await repo.update_build(
            build_id,
            eval_passed=passed,
            eval_faithfulness=0.0,  # 추후 실제 평가 시 업데이트
            eval_relevancy=0.0,
        )

        return passed

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
    ) -> None:
        """S3 배포."""
        from src.distill.deployer import DistillDeployer

        deployer = DistillDeployer(profile)
        build = await repo.get_build(build_id)
        version = build["version"]

        s3_uri = await deployer.upload_to_s3(gguf_path, version)
        await deployer.create_and_upload_manifest(s3_uri, version, build)

        await repo.update_build(
            build_id,
            s3_uri=s3_uri,
            deployed_at=datetime.now(timezone.utc),
        )
