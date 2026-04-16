"""Tests for scripts/coverage_gate.py (PR6)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ 는 sys.path 에 없으므로 직접 import
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))
import coverage_gate  # type: ignore  # noqa: E402


class TestFilterSrcPython:
    def test_accepts_src_py(self):
        assert coverage_gate.filter_src_python(["src/foo/bar.py"]) == ["src/foo/bar.py"]

    def test_rejects_dashboard(self):
        assert coverage_gate.filter_src_python(["dashboard/pages/app.py"]) == []

    def test_rejects_tests(self):
        assert coverage_gate.filter_src_python(["tests/unit/test_foo.py"]) == []

    def test_rejects_scripts(self):
        assert coverage_gate.filter_src_python(["scripts/util.py"]) == []

    def test_rejects_non_py(self):
        assert coverage_gate.filter_src_python([
            "src/foo/data.json",
            "src/bar.yaml",
            "docs/README.md",
        ]) == []

    def test_rejects_init(self):
        """__init__.py 는 대부분 facade — 커버리지 의미 없음."""
        assert coverage_gate.filter_src_python(["src/foo/__init__.py"]) == []

    def test_rejects_exempt(self):
        """EXEMPT_FILES 에 등록된 파일 제외."""
        for exempt in coverage_gate.EXEMPT_FILES:
            assert coverage_gate.filter_src_python([exempt]) == []

    def test_mixed_list(self):
        result = coverage_gate.filter_src_python([
            "src/foo/bar.py",
            "dashboard/app.py",
            "src/baz/qux.py",
            "tests/unit/test_foo.py",
            "README.md",
        ])
        assert result == ["src/foo/bar.py", "src/baz/qux.py"]


class TestLoadReport:
    def test_valid_report(self, tmp_path):
        report = tmp_path / "coverage.json"
        report.write_text(json.dumps({"files": {"src/x.py": {"summary": {"percent_covered": 95.0}}}}))
        data = coverage_gate.load_report(str(report))
        assert "files" in data

    def test_missing_report_exits_2(self, tmp_path, capsys):
        missing = tmp_path / "nope.json"
        with pytest.raises(SystemExit) as exc:
            coverage_gate.load_report(str(missing))
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "coverage report not found" in err

    def test_invalid_json_exits_2(self, tmp_path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json}")
        with pytest.raises(SystemExit) as exc:
            coverage_gate.load_report(str(bad))
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "invalid JSON" in err


class TestMain:
    """End-to-end of coverage_gate.main via monkeypatching."""

    def _write_report(self, tmp_path: Path, files: dict) -> Path:
        report = tmp_path / "coverage.json"
        report.write_text(json.dumps({"files": files}))
        return report

    def test_no_changed_files_returns_0(self, tmp_path, monkeypatch):
        monkeypatch.setattr(coverage_gate, "git_changed_files", lambda base: [])
        monkeypatch.setattr(sys, "argv", [
            "coverage_gate.py", "--report", str(tmp_path / "coverage.json"),
        ])
        assert coverage_gate.main() == 0

    def test_all_above_threshold_returns_0(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(coverage_gate, "git_changed_files", lambda base: [
            "src/foo/good.py",
            "dashboard/skip.py",  # filtered out
        ])
        report = self._write_report(tmp_path, {
            "src/foo/good.py": {
                "summary": {"percent_covered": 85.0, "num_statements": 100},
            },
        })
        monkeypatch.setattr(sys, "argv", [
            "coverage_gate.py", "--report", str(report), "--threshold", "80",
        ])
        assert coverage_gate.main() == 0
        out = capsys.readouterr().out
        assert "✅" in out
        assert "85.0%" in out

    def test_below_threshold_returns_1(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(coverage_gate, "git_changed_files", lambda base: [
            "src/foo/weak.py",
        ])
        report = self._write_report(tmp_path, {
            "src/foo/weak.py": {
                "summary": {"percent_covered": 60.0, "num_statements": 200},
            },
        })
        monkeypatch.setattr(sys, "argv", [
            "coverage_gate.py", "--report", str(report), "--threshold", "80",
        ])
        assert coverage_gate.main() == 1
        out = capsys.readouterr().out
        assert "❌" in out
        assert "60.0%" in out
        assert "below" in out.lower()

    def test_missing_file_in_report_flagged(self, tmp_path, monkeypatch, capsys):
        """파일이 변경됐는데 coverage report 에 없으면 failure 로 집계."""
        monkeypatch.setattr(coverage_gate, "git_changed_files", lambda base: [
            "src/foo/new_module.py",
        ])
        report = self._write_report(tmp_path, {})  # empty files
        monkeypatch.setattr(sys, "argv", [
            "coverage_gate.py", "--report", str(report), "--threshold", "80",
        ])
        assert coverage_gate.main() == 1
        out = capsys.readouterr().out
        assert "not in coverage report" in out
