"""K8s manifest 정적 검증 — PR-8 (M).

핵심 Deployment 가 RollingUpdate strategy + maxUnavailable=0 + minReadySeconds
을 명시했는지 정적으로 확인. 무중단 배포의 사고 방지 가드.
"""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_PATHS = [
    REPO_ROOT / "deploy/k8s/api/deployment.yaml",
    REPO_ROOT / "deploy/k8s/dashboard/deployment.yaml",
]


def _iter_documents(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for doc in yaml.safe_load_all(f):
            if doc:
                yield doc


def _find_deployment_spec(path: Path) -> dict | None:
    for doc in _iter_documents(path):
        if doc.get("kind") == "Deployment":
            return doc.get("spec", {})
    return None


class TestRollingUpdate:
    def test_api_deployment_has_zero_max_unavailable(self):
        spec = _find_deployment_spec(REPO_ROOT / "deploy/k8s/api/deployment.yaml")
        assert spec is not None
        strategy = spec.get("strategy", {})
        assert strategy.get("type") == "RollingUpdate"
        ru = strategy.get("rollingUpdate", {})
        assert ru.get("maxUnavailable") == 0
        assert ru.get("maxSurge") in (1, "1", "25%")
        assert spec.get("minReadySeconds") == 10

    def test_dashboard_deployment_has_zero_max_unavailable(self):
        spec = _find_deployment_spec(
            REPO_ROOT / "deploy/k8s/dashboard/deployment.yaml"
        )
        assert spec is not None
        strategy = spec.get("strategy", {})
        assert strategy.get("type") == "RollingUpdate"
        ru = strategy.get("rollingUpdate", {})
        assert ru.get("maxUnavailable") == 0
        assert spec.get("minReadySeconds") == 10
