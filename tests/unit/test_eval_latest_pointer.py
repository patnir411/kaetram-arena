"""Regression tests for the portable latest-evaluation pointer."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import dashboard.api as dashboard_api
from dashboard.eval_latest import (
    LatestEvalPointerError,
    promote_latest_eval_run,
    resolve_latest_eval_dir,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _eval_tree(tmp_path: Path, run_tag: str = "20260723_120000") -> tuple[Path, Path]:
    eval_dir = tmp_path / "eval"
    run_dir = eval_dir / "runs" / run_tag
    run_dir.mkdir(parents=True)
    return eval_dir, run_dir


def test_promote_writes_atomic_regular_relative_pointer(tmp_path: Path) -> None:
    eval_dir, run_dir = _eval_tree(tmp_path)

    relative = promote_latest_eval_run(eval_dir, run_dir)
    pointer = eval_dir / "latest-run.txt"

    assert relative == "runs/20260723_120000"
    assert pointer.read_text() == "runs/20260723_120000\n"
    assert stat.S_ISREG(pointer.lstat().st_mode)
    assert not pointer.is_symlink()
    assert not list(eval_dir.glob(".latest-run.*.tmp"))
    assert resolve_latest_eval_dir(eval_dir) == run_dir.resolve()


def test_promotion_replaces_existing_regular_pointer(tmp_path: Path) -> None:
    eval_dir, first_run = _eval_tree(tmp_path, "first")
    second_run = eval_dir / "runs" / "second"
    second_run.mkdir()
    promote_latest_eval_run(eval_dir, first_run)

    promote_latest_eval_run(eval_dir, second_run)

    assert (eval_dir / "latest-run.txt").read_text() == "runs/second\n"
    assert resolve_latest_eval_dir(eval_dir) == second_run.resolve()


@pytest.mark.parametrize(
    "payload",
    [
        "../outside\n",
        "/tmp/outside\n",
        "runs/nested/run\n",
        "other/run\n",
        "runs/run\nsecond-line\n",
        "runs/run tag\n",
    ],
)
def test_resolver_rejects_unsafe_pointer_values(
    tmp_path: Path, payload: str
) -> None:
    eval_dir, _ = _eval_tree(tmp_path, "run")
    (eval_dir / "latest-run.txt").write_text(payload)

    with pytest.raises(LatestEvalPointerError):
        resolve_latest_eval_dir(eval_dir)


def test_resolver_rejects_symlink_pointer_and_target(tmp_path: Path) -> None:
    eval_dir, run_dir = _eval_tree(tmp_path, "run")
    pointer = eval_dir / "latest-run.txt"
    pointer.symlink_to(Path("runs") / run_dir.name)
    with pytest.raises(LatestEvalPointerError, match="regular file"):
        resolve_latest_eval_dir(eval_dir)

    pointer.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    run_dir.rmdir()
    run_dir.symlink_to(outside, target_is_directory=True)
    pointer.write_text("runs/run\n")
    with pytest.raises(LatestEvalPointerError, match="must not be a symlink"):
        resolve_latest_eval_dir(eval_dir)


def test_missing_pointer_allows_legacy_dashboard_fallback(tmp_path: Path) -> None:
    eval_dir, _ = _eval_tree(tmp_path)
    assert resolve_latest_eval_dir(eval_dir) is None


def test_run_eval_uses_text_pointer_helper_not_directory_symlink() -> None:
    launcher = (REPO_ROOT / "scripts" / "run-eval.sh").read_text()

    assert 'dashboard/eval_latest.py" promote' in launcher
    assert "dataset/eval/latest-run.txt" in launcher
    assert "ln -sfn" not in launcher
    assert "dataset/eval/latest →" not in launcher


class _APIProbe(dashboard_api.APIMixin):
    def _send_json(self, payload):
        self.payload = payload
        return payload


def test_dashboard_reads_results_from_promoted_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_dir = tmp_path / "dataset"
    eval_dir = dataset_dir / "eval"
    run_dir = eval_dir / "runs" / "new-run"
    model_dir = run_dir / "base"
    model_dir.mkdir(parents=True)
    (model_dir / "results.json").write_text(
        '{"meta":{"model":"base","scenario":"D","total_episodes":1,'
        '"ok_episodes":1},"metrics":{},"episodes":[{"status":"ok"}]}'
    )
    # A stale legacy result must not be selected when a valid pointer exists.
    stale_dir = eval_dir / "stale"
    stale_dir.mkdir()
    (stale_dir / "results.json").write_text(
        '{"meta":{"model":"stale"},"metrics":{},"episodes":[]}'
    )
    promote_latest_eval_run(eval_dir, run_dir)
    monkeypatch.setattr(dashboard_api, "DATASET_DIR", os.fspath(dataset_dir))
    dashboard_api.APIMixin._eval_cache = {"data": None, "mtime": 0}

    probe = _APIProbe()
    probe.send_eval_latest()

    assert probe.payload["status"] == "ok"
    assert [model["name"] for model in probe.payload["models"]] == ["base"]


def test_dashboard_fails_closed_on_invalid_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_dir = tmp_path / "dataset"
    eval_dir, _ = _eval_tree(dataset_dir, "run")
    (eval_dir / "latest-run.txt").write_text("../outside\n")
    monkeypatch.setattr(dashboard_api, "DATASET_DIR", os.fspath(dataset_dir))

    probe = _APIProbe()
    probe.send_eval_latest()

    assert probe.payload == {
        "status": "invalid_latest_pointer",
        "models": [],
    }


def test_live_dashboard_reads_watchdog_from_promoted_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_dir = tmp_path / "dataset"
    eval_dir = dataset_dir / "eval"
    run_dir = eval_dir / "runs" / "new-run"
    run_dir.mkdir(parents=True)
    (run_dir / "watchdog_status.json").write_text(
        '{"timestamp":"2026-07-23T12:00:00-0400","models":{}}'
    )
    (run_dir / "watchdog_alert.txt").write_text("test alert\n")
    promote_latest_eval_run(eval_dir, run_dir)
    monkeypatch.setattr(dashboard_api, "DATASET_DIR", os.fspath(dataset_dir))
    monkeypatch.setattr(dashboard_api.glob, "glob", lambda pattern: [])
    dashboard_api.APIMixin._eval_live_cache = {
        "data": None,
        "computed_at": 0,
        "fingerprint": None,
        "eval_fingerprint": None,
    }

    probe = _APIProbe()
    probe.send_eval_live()

    assert probe.payload["watchdog"] == {
        "timestamp": "2026-07-23T12:00:00-0400",
        "models": {},
    }
    assert probe.payload["watchdog_alert"] == "test alert"
    assert probe.payload["latest_pointer_status"] == "promoted"


def test_live_dashboard_cache_tracks_pointer_and_watchdog_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_dir = tmp_path / "dataset"
    eval_dir = dataset_dir / "eval"
    first_run = eval_dir / "runs" / "first"
    second_run = eval_dir / "runs" / "second"
    first_run.mkdir(parents=True)
    second_run.mkdir()
    (first_run / "watchdog_status.json").write_text('{"run":"first"}')
    (first_run / "watchdog_alert.txt").write_text("first alert\n")
    (second_run / "watchdog_status.json").write_text('{"run":"second"}')
    (second_run / "watchdog_alert.txt").write_text("second alert\n")
    promote_latest_eval_run(eval_dir, first_run)
    monkeypatch.setattr(dashboard_api, "DATASET_DIR", os.fspath(dataset_dir))
    monkeypatch.setattr(dashboard_api.glob, "glob", lambda pattern: [])
    dashboard_api.APIMixin._eval_live_cache = {
        "data": None,
        "computed_at": 0,
        "fingerprint": None,
        "eval_fingerprint": None,
    }
    probe = _APIProbe()

    probe.send_eval_live()
    assert probe.payload["watchdog"] == {"run": "first"}

    promote_latest_eval_run(eval_dir, second_run)
    probe.send_eval_live()
    assert probe.payload["watchdog"] == {"run": "second"}
    assert probe.payload["watchdog_alert"] == "second alert"

    (second_run / "watchdog_alert.txt").write_text("updated alert content\n")
    probe.send_eval_live()
    assert probe.payload["watchdog_alert"] == "updated alert content"

    (eval_dir / "latest-run.txt").write_text("../outside\n")
    probe.send_eval_live()
    assert probe.payload["watchdog"] is None
    assert probe.payload["watchdog_alert"] == ""
    assert probe.payload["latest_pointer_status"] == "invalid"
    assert "direct runs/<run-tag>" in probe.payload["latest_pointer_error"]
