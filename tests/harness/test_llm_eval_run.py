"""CLI tests for validation/llm_eval_run.py -- no live LLM."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_runner():
    path = Path(__file__).resolve().parents[2] / "validation" / "llm_eval_run.py"
    spec = importlib.util.spec_from_file_location("llm_eval_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_grounded_comparison_with_criteria_extraction_exits_with_clear_error(capsys, monkeypatch):
    runner = _load_runner()
    called: list[str] = []

    def boom(*args, **kwargs):
        called.append("ran")
        raise AssertionError("must not run criteria extraction when the flag is invalid")

    monkeypatch.setattr(runner, "run_criteria_extraction", boom)
    code = runner.main(["--subsystem", "criteria_extraction", "--grounded-comparison"])
    captured = capsys.readouterr()
    combined = (captured.err + captured.out).lower()
    assert code != 0
    assert called == []
    assert "grounded-comparison" in combined or "ungrounded" in combined
    assert "criteria_extraction" in combined


def test_grounded_comparison_with_intake_exits_with_clear_error(capsys, monkeypatch):
    runner = _load_runner()
    called: list[str] = []

    def boom(*args, **kwargs):
        called.append("ran")
        raise AssertionError("must not run intake when the flag is invalid")

    monkeypatch.setattr(runner, "run_intake_eval", boom)
    code = runner.main(["--subsystem", "intake", "--grounded-comparison"])
    captured = capsys.readouterr()
    combined = (captured.err + captured.out).lower()
    assert code != 0
    assert called == []
    assert "grounded-comparison" in combined or "ungrounded" in combined
    assert "intake" in combined
