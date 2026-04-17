"""Build pipeline executor — orchestrates generate→train→quantize→evaluate→deploy.

Extracted from ``DistillService.run_pipeline`` for testability and SRP.
``DistillService`` still owns the step implementations; this class owns the
orchestration flow and error handling.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.distill.repository import DistillRepository
    from src.distill.service import DistillService

logger = logging.getLogger(__name__)

# GPU 원격 학습 완료 마커 — 로컬 양자화/배포 스킵 판단에 사용
_GPU_TRAINED = "__GPU_TRAINED__"


class BuildPipelineExecutor:
    """Orchestrates a single distill build run.

    Usage::

        executor = BuildPipelineExecutor(service, repo, build_id, profile_name)
        await executor.run(steps=["generate", "train", "evaluate", "deploy"])
    """

    def __init__(
        self,
        service: DistillService,
        repo: DistillRepository,
        build_id: str,
        profile_name: str,
        *,
        use_curated_data: bool = False,
    ) -> None:
        self._svc = service
        self._repo = repo
        self._build_id = build_id
        self._profile_name = profile_name
        self._use_curated_data = use_curated_data

    async def run(self, steps: list[str] | None = None) -> None:
        """Execute the build pipeline with error handling and cleanup."""
        profile_dict = await self._repo.get_profile(self._profile_name)
        if not profile_dict:
            await self._repo.update_build(
                self._build_id, status="failed",
                error_message=f"Profile not found: {self._profile_name}",
            )
            return

        from src.distill.config import dict_to_profile
        profile = dict_to_profile(profile_dict)

        # 평가는 절대 스킵 불가 — 품질 게이트 없이 배포하면 사고 발생
        all_steps = steps or ["generate", "train", "quantize", "evaluate", "deploy"]
        if "evaluate" not in all_steps:
            logger.warning("Evaluate step requested-skip rejected — forcing evaluate")
            all_steps = list(all_steps) + ["evaluate"]

        from src.config import get_settings
        work_dir = Path(get_settings().distill.work_dir)
        build_dir = work_dir / self._build_id
        build_dir.mkdir(parents=True, exist_ok=True)

        try:
            data_path = str(build_dir / "train.jsonl")
            model_path = str(build_dir / "model" / "merged")
            gguf_path = str(build_dir / "model.gguf")

            if "generate" in all_steps:
                await self._repo.update_build(self._build_id, status="generating")
                data_path = await self._svc._generate_data(
                    self._build_id, self._profile_name, profile, self._repo, build_dir,
                    use_curated_data=self._use_curated_data,
                )

            if "train" in all_steps:
                await self._repo.update_build(self._build_id, status="training")
                model_path = await self._svc._train(
                    self._build_id, profile, data_path, self._repo, build_dir,
                )

            gpu_trained = model_path == _GPU_TRAINED

            if "quantize" in all_steps and not gpu_trained:
                await self._repo.update_build(self._build_id, status="quantizing")
                gguf_path = await self._svc._quantize(
                    self._build_id, profile, model_path, self._repo, build_dir,
                )

            # 평가 (배포 전 반드시 실행)
            await self._repo.update_build(self._build_id, status="evaluating")
            eval_gguf_path = gguf_path
            if gpu_trained:
                eval_gguf_path = await self._svc._download_gguf_from_s3(
                    self._build_id, profile, build_dir,
                )
            passed = await self._svc._evaluate(
                self._build_id, profile, model_path, data_path, self._repo,
                gguf_path=eval_gguf_path,
            )
            if not passed:
                await self._repo.update_build(
                    self._build_id, status="failed",
                    error_message="Evaluation below threshold — deploy skipped",
                    error_step="evaluate",
                )
                logger.error("Build %s failed evaluation — NOT deploying", self._build_id)
                return

            if "deploy" in all_steps:
                await self._repo.update_build(self._build_id, status="deploying")
                await self._svc._deploy(
                    self._build_id, profile, gguf_path, self._repo,
                    gpu_trained=gpu_trained,
                )

            await self._repo.update_build(self._build_id, status="completed")
            logger.info("Build %s completed successfully", self._build_id)

        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error("Build %s failed: %s", self._build_id, e)
            await self._repo.update_build(
                self._build_id, status="failed",
                error_message=str(e)[:1000],
            )
        finally:
            if build_dir.exists():
                shutil.rmtree(build_dir, ignore_errors=True)
