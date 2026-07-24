import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from scripts import arm_stats


def test_collect_arm_treats_missing_agent_root_as_missing_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    assert arm_stats.collect_arm("r10-base-9B", tmp_path) == []
    assert "arm quarantined" in capsys.readouterr().err


def test_collect_arm_quarantines_any_partial_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    def incomplete(*args, **kwargs):
        raise arm_stats.MissingEvidenceError("one declared lane is missing")

    monkeypatch.setattr(arm_stats, "require_agent_run_logs", incomplete)
    assert arm_stats.collect_arm("opd-r2", tmp_path) == []
    stderr = capsys.readouterr().err
    assert "opd-r2" in stderr
    assert "one declared lane is missing" in stderr


def test_verify_fails_closed_before_parsing_when_artifacts_are_missing(
    tmp_path: Path,
    capsys,
) -> None:
    assert arm_stats.main(["--verify", "--raw-root", str(tmp_path)]) == 2
    stderr = capsys.readouterr().err
    assert "ERROR:" in stderr
    assert "arm_stats r10 verification" in stderr


def test_protocol_cutoff_uses_offset_aware_start_and_registered_budget(
    tmp_path: Path,
) -> None:
    (tmp_path / "run.meta.json").write_text(
        json.dumps({
            "started_at": "2026-07-11T06:54:35-04:00",
            "hours_budget": 6.0,
        }),
        encoding="utf-8",
    )

    assert arm_stats._protocol_cutoff(tmp_path, 6.0) == datetime(
        2026, 7, 11, 16, 54, 35, tzinfo=timezone.utc,
    )


def test_protocol_cutoff_rejects_mismatched_or_naive_metadata(tmp_path: Path) -> None:
    meta = tmp_path / "run.meta.json"
    meta.write_text(
        json.dumps({
            "started_at": "2026-07-11T06:54:35-04:00",
            "hours_budget": 8.0,
        }),
        encoding="utf-8",
    )
    try:
        arm_stats._protocol_cutoff(tmp_path, 6.0)
    except ValueError as exc:
        assert "hours_budget" in str(exc)
    else:
        raise AssertionError("mismatched protocol budget must fail closed")

    meta.write_text(
        json.dumps({
            "started_at": "2026-07-11T06:54:35",
            "hours_budget": 6.0,
        }),
        encoding="utf-8",
    )
    try:
        arm_stats._protocol_cutoff(tmp_path, 6.0)
    except ValueError as exc:
        assert "explicit UTC offset" in str(exc)
    else:
        raise AssertionError("naive protocol start must fail closed")

    meta.write_text(
        json.dumps({
            "started_at": "2026-07-11T06:54:35-04:00",
            "hours_budget": True,
        }),
        encoding="utf-8",
    )
    try:
        arm_stats._protocol_cutoff(tmp_path, 6.0)
    except ValueError as exc:
        assert "numeric hours_budget" in str(exc)
    else:
        raise AssertionError("boolean protocol budget must fail closed")


def test_july_registry_contains_every_recovered_mechanism_arm() -> None:
    expected = {
        "run_20260711_065435",
        "run_20260711_153427",
        "run_20260713_084905",
        "run_20260713_191230",
        "run_20260715_030342",
        "run_20260715_090731",
        "run_20260715_151045",
        "run_20260715_211431",
        "run_20260716_215512",
    }
    registered = {
        run_id
        for arm in arm_stats.ARMS.values()
        if arm["block"] == "hardening"
        for run_id in arm["runs"]
    }
    assert registered == expected


def test_protocol_score_requires_zero_visible_core3_progress() -> None:
    canonical = SimpleNamespace(first_observe_in_run=lambda: {
        "active_quests": [],
        "finished_quests": [{"name": "Miner's Quest"}],
    })
    progressed = SimpleNamespace(first_observe_in_run=lambda: {
        "active_quests": [{"name": "Herbalist's Desperation", "stage": 1}],
        "finished_quests": [],
    })
    missing = SimpleNamespace(first_observe_in_run=lambda: None)

    assert arm_stats._has_zero_visible_core3_start(canonical)
    assert not arm_stats._has_zero_visible_core3_start(progressed)
    assert not arm_stats._has_zero_visible_core3_start(missing)
