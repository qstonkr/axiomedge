"""gpu_trainer EC2 lifecycle — boto3 전환 검증."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_ec2():
    return MagicMock()


@pytest.mark.asyncio
async def test_get_instance_state_uses_boto3(mock_ec2) -> None:
    """describe_instances 호출 + state.name 추출."""
    mock_ec2.describe_instances.return_value = {
        "Reservations": [
            {"Instances": [{"State": {"Name": "running"}}]},
        ],
    }
    with patch("src.distill.gpu_trainer._ec2_client", return_value=mock_ec2):
        from src.distill.gpu_trainer import _get_instance_state
        state = await _get_instance_state("i-abc123")

    assert state == "running"
    mock_ec2.describe_instances.assert_called_once_with(InstanceIds=["i-abc123"])


@pytest.mark.asyncio
async def test_start_instance_calls_boto3_start_then_waits_running(mock_ec2) -> None:
    """start_instances + 폴링으로 running 까지 대기."""
    states = iter(["pending", "pending", "running"])
    mock_ec2.describe_instances.side_effect = lambda **kw: {
        "Reservations": [{"Instances": [{"State": {"Name": next(states)}}]}],
    }

    with patch("src.distill.gpu_trainer._ec2_client", return_value=mock_ec2), \
         patch("src.distill.gpu_trainer.asyncio.sleep", return_value=None):
        from src.distill.gpu_trainer import _start_instance
        ok = await _start_instance("i-abc123")

    assert ok is True
    mock_ec2.start_instances.assert_called_once_with(InstanceIds=["i-abc123"])


@pytest.mark.asyncio
async def test_stop_instance_calls_boto3_stop(mock_ec2) -> None:
    """stop_instances fire-and-forget — waiter 없이 호출만."""
    with patch("src.distill.gpu_trainer._ec2_client", return_value=mock_ec2):
        from src.distill.gpu_trainer import _stop_instance
        await _stop_instance("i-abc123")

    mock_ec2.stop_instances.assert_called_once_with(InstanceIds=["i-abc123"])


def test_no_subprocess_in_module() -> None:
    """boto3 전환 후 subprocess shell 호출 제거 — module source 검사."""
    import src.distill.gpu_trainer as gpu_trainer_module

    src_text = open(gpu_trainer_module.__file__, encoding="utf-8").read()
    assert "asyncio.create_subprocess_shell" not in src_text
    assert "aws ec2" not in src_text
