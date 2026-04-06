"""Distill 파이프라인 오케스트레이터.

데이터 생성 → 학습 → 평가 → 양자화 → 배포를 subprocess로 격리 실행.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
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

    async def run_pipeline(
        self,
        build_id: str,
        profile_name: str,
        steps: list[str] | None = None,
    ) -> None:
        """전체 파이프라인 실행 (별도 프로세스 또는 in-process)."""
        repo = DistillRepository(self.session_factory)
        profile = self.config.profiles.get(profile_name)
        if not profile:
            await repo.update_build(build_id, status="failed",
                                    error_message=f"Profile not found: {profile_name}")
            return

        all_steps = steps or ["generate", "train", "evaluate", "quantize", "deploy"]
        build_dir = Path(f"/tmp/distill/{build_id}")
        build_dir.mkdir(parents=True, exist_ok=True)

        try:
            data_path = str(build_dir / "train.jsonl")
            model_path = str(build_dir / "model" / "merged")
            gguf_path = str(build_dir / "model.gguf")

            # Step 1: 데이터 생성
            if "generate" in all_steps:
                await repo.update_build(build_id, status="generating")
                data_path = await self._generate_data(build_id, profile, repo, build_dir)

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
        self, build_id: str, profile: DistillProfile,
        repo: DistillRepository, build_dir: Path,
    ) -> str:
        """QA 데이터 생성."""
        from src.database.repositories.search_group import SearchGroupRepository
        from src.distill.data_generator import DistillDataGenerator

        generator = DistillDataGenerator(
            self.llm, self.embedder, profile, self.qdrant_url,
        )

        # search_group → KB IDs
        group_repo = SearchGroupRepository(self.session_factory)
        kb_ids = await group_repo.resolve_kb_ids(group_name=profile.search_group)
        if not kb_ids:
            raise ValueError(f"Search group '{profile.search_group}' has no KBs")

        # 청크에서 QA 생성
        chunk_qa = await generator.generate_from_chunks(kb_ids)

        # Usage log에서 QA 추출
        log_qa = await generator.generate_from_usage_logs(
            self.session_factory, kb_ids, profile.search_group,
        )

        # 재학습 데이터 (DB에서 — profile_name은 빌드 프로필 이름)
        # _generate_data는 profile 객체를 받지만 profile_name은 별도 전달 필요
        profile_key = next(
            (k for k, v in self.config.profiles.items() if v == profile), ""
        )
        retrain_result = await repo.list_training_data(
            profile_name=profile_key, source_type="retrain", limit=5000,
        )
        retrain_qa = retrain_result.get("items", [])

        # 병합 + 중복 제거
        all_qa = await generator.merge_and_deduplicate(chunk_qa, log_qa, retrain_qa)

        # Augmentation
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
        min_samples = self.config.defaults.min_training_samples
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
            quantize_method=profile.deploy.quantize,
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
