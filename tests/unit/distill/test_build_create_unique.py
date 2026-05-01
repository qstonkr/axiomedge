"""DistillBuildRepository.create_unique — advisory lock 기반 동시 빌드 차단.

같은 profile 의 active 빌드(pending/generating/training/quantizing/evaluating/
deploying) 가 있을 때 새 build 생성을 거부 → 동시 빌드로 인한 GPU 자원 충돌
+ deploy race 차단.

advisory lock 자체는 PostgreSQL 전용이라 unit test 에서 효과 검증은 mock 으로.
실제 lock contention 검증은 integration test 영역.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def session_maker():
    """async_sessionmaker 모방 — async with 진입 시 mock session 반환."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.rollback = AsyncMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    maker = MagicMock(return_value=cm)
    maker._session = session
    return maker


@pytest.mark.asyncio
async def test_create_unique_blocks_when_active_build_exists(
    session_maker,
) -> None:
    """active 빌드가 1개 이상이면 RuntimeError raise."""
    from src.distill.repositories.build import DistillBuildRepository

    count_result = MagicMock()
    count_result.scalar = MagicMock(return_value=1)
    session_maker._session.execute = AsyncMock(side_effect=[
        MagicMock(),       # pg_advisory_xact_lock
        count_result,      # SELECT count(*)
    ])

    repo = DistillBuildRepository(session_maker)

    with pytest.raises(RuntimeError, match="active build exists"):
        await repo.create_unique(
            profile_name="p1", id="b1", base_model="m", status="pending",
        )


@pytest.mark.asyncio
async def test_create_unique_creates_when_no_active_build(
    session_maker,
) -> None:
    """active 빌드 0개면 정상 생성."""
    from src.distill.repositories.build import DistillBuildRepository

    count_result = MagicMock()
    count_result.scalar = MagicMock(return_value=0)
    session_maker._session.execute = AsyncMock(side_effect=[
        MagicMock(),       # pg_advisory_xact_lock
        count_result,      # SELECT count(*)
    ])

    repo = DistillBuildRepository(session_maker)

    result = await repo.create_unique(
        profile_name="p1", id="b1", base_model="m", status="pending",
    )

    session_maker._session.add.assert_called_once()
    session_maker._session.commit.assert_awaited()
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_create_unique_acquires_advisory_lock_first(
    session_maker,
) -> None:
    """첫 SQL 호출이 pg_advisory_xact_lock 이어야 — race 방지."""
    from src.distill.repositories.build import DistillBuildRepository

    count_result = MagicMock()
    count_result.scalar = MagicMock(return_value=0)
    session_maker._session.execute = AsyncMock(side_effect=[
        MagicMock(),
        count_result,
    ])

    repo = DistillBuildRepository(session_maker)
    await repo.create_unique(
        profile_name="p1", id="b1", base_model="m", status="pending",
    )

    first_call = session_maker._session.execute.call_args_list[0]
    sql_arg = first_call.args[0]
    assert "pg_advisory_xact_lock" in str(sql_arg).lower()


@pytest.mark.asyncio
async def test_mark_build_deployed_clears_other_deployed(session_maker) -> None:
    """mark_build_deployed 가 같은 profile 의 다른 deployed_at 을 NULL 로 정리."""
    from src.distill.repositories.build import DistillBuildRepository

    target_model = MagicMock()
    target_model.id = "b1"
    fetch_result = MagicMock()
    fetch_result.scalar_one_or_none = MagicMock(return_value=target_model)
    session_maker._session.execute = AsyncMock(side_effect=[
        MagicMock(),    # UPDATE clear other deployed_at
        MagicMock(),    # UPDATE target deployed_at
        fetch_result,   # SELECT target row
    ])

    repo = DistillBuildRepository(session_maker)
    # _to_dict 가 model attrs 에 의존 — minimal mock setup
    target_model.profile_name = "p1"
    target_model.status = "deployed"
    target_model.version = "v1"
    target_model.search_group = ""
    target_model.base_model = "m"
    target_model.training_samples = None
    target_model.data_sources = None
    target_model.train_loss = None
    target_model.eval_loss = None
    target_model.training_duration_sec = None
    target_model.eval_faithfulness = None
    target_model.eval_relevancy = None
    target_model.eval_passed = None
    target_model.gguf_size_mb = None
    target_model.gguf_sha256 = None
    target_model.model_name = None
    target_model.quantize_method = None
    target_model.s3_uri = None
    target_model.deployed_at = None
    target_model.rollback_from = None
    target_model.force_deploy = False
    target_model.error_message = None
    target_model.error_step = None
    target_model.gpu_instance_id = None
    target_model.gpu_started_at = None
    target_model.s3_result_key = None
    target_model.last_sweep_at = None
    target_model.gpu_finished_at = None
    target_model.created_at = None
    target_model.updated_at = None

    result = await repo.mark_build_deployed("b1", "p1")
    assert result is not None
    # 두 UPDATE 호출 + 하나의 SELECT
    assert session_maker._session.execute.call_count == 3
    session_maker._session.commit.assert_awaited()
