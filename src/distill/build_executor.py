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
        """Backward-compatible wrapper. local 모드면 pre + post 같이, GPU 모드면
        pre 후 종료 (sweeper 가 post 호출).
        """
        result = await self.run_pre_train(steps=steps)
        if result["phase"] == "awaiting_gpu":
            return
        await self.run_post_train(result["train_result"], steps=steps)

    async def run_pre_train(
        self, steps: list[str] | None = None,
    ) -> dict:
        """generate + train 시작까지 실행.

        반환:
            {"phase": "awaiting_gpu", "build_id": ...} — GPU 학습 시작, sweeper
                가 이어받음. caller (arq job) 는 즉시 종료.
            {"phase": "post_train_ready", "train_result": {...}, "steps": [...]} —
                local mode 또는 train 없음. caller 가 run_post_train 호출.
            {"phase": "failed"} — error 발생, build status 이미 failed 로 update.
        """
        prep = await self._prepare(steps)
        if prep is None:
            return {"phase": "failed"}
        profile, all_steps, build_dir = prep

        try:
            data_path = str(build_dir / "train.jsonl")
            model_path = str(build_dir / "model" / "merged")

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

            if model_path == _GPU_TRAINED:
                # GPU async — sweeper 가 결과 detect 후 post_train enqueue.
                # build_dir 는 의도적으로 남김 — 부분 cleanup 은 sweeper 책임
                # (post_train 의 finally 가 cleanup 담당).
                logger.info(
                    "Build %s awaiting GPU training (sweeper takes over)",
                    self._build_id,
                )
                return {"phase": "awaiting_gpu", "build_id": self._build_id}

            # Local mode — train_result 만들어서 post_train 으로 위임.
            return {
                "phase": "post_train_ready",
                "train_result": {
                    "gpu_trained": False,
                    "model_path": model_path,
                    "data_path": data_path,
                },
            }

        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error("Build %s pre_train failed: %s", self._build_id, e)
            await self._repo.update_build(
                self._build_id, status="failed",
                error_message=str(e)[:1000],
            )
            if build_dir.exists():
                shutil.rmtree(build_dir, ignore_errors=True)
            return {"phase": "failed"}

    async def run_post_train(
        self, train_result: dict, steps: list[str] | None = None,
    ) -> None:
        """evaluate + quantize + deploy + cleanup. sweeper / pre_train wrapper 가 호출.

        train_result:
            {"gpu_trained": True, "result_json": {...}} — GPU 학습 결과 (S3 메타).
            {"gpu_trained": False, "model_path": "..."} — local 학습 결과.
        """
        prep = await self._prepare(steps)
        if prep is None:
            return
        profile, all_steps, build_dir = prep

        gpu_trained = bool(train_result.get("gpu_trained"))
        model_path = train_result.get("model_path") or str(
            build_dir / "model" / "merged",
        )
        data_path = train_result.get("data_path") or str(build_dir / "train.jsonl")
        gguf_path = str(build_dir / "model.gguf")

        try:
            if "quantize" in all_steps and not gpu_trained:
                await self._repo.update_build(self._build_id, status="quantizing")
                gguf_path = await self._svc._quantize(
                    self._build_id, profile, model_path, self._repo, build_dir,
                )

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
            logger.error("Build %s post_train failed: %s", self._build_id, e)
            await self._repo.update_build(
                self._build_id, status="failed",
                error_message=str(e)[:1000],
            )
        finally:
            if build_dir.exists():
                shutil.rmtree(build_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # Internal — pre/post 양쪽이 공유하는 setup (profile, steps, build_dir)
    # -----------------------------------------------------------------------

    async def _prepare(self, steps: list[str] | None):
        """profile 로드 + steps 정규화 + build_dir 보장.

        Returns ``(profile, all_steps, build_dir)`` or None on profile-not-found.
        """
        profile_dict = await self._repo.get_profile(self._profile_name)
        if not profile_dict:
            await self._repo.update_build(
                self._build_id, status="failed",
                error_message=f"Profile not found: {self._profile_name}",
            )
            return None

        from src.distill.config import dict_to_profile
        profile = dict_to_profile(profile_dict)

        # 평가는 절대 스킵 불가 — 품질 게이트 없이 배포하면 사고 발생
        all_steps = list(steps) if steps else ["generate", "train", "quantize", "evaluate", "deploy"]
        if "evaluate" not in all_steps:
            logger.warning("Evaluate step requested-skip rejected — forcing evaluate")
            all_steps.append("evaluate")

        from src.config import get_settings
        work_dir = Path(get_settings().distill.work_dir)
        build_dir = work_dir / self._build_id
        build_dir.mkdir(parents=True, exist_ok=True)
        return profile, all_steps, build_dir
