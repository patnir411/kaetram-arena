"""Database-lane contract for evaluation resets."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import eval_harness


def test_run_eval_reset_reuses_harness_collection_contract() -> None:
    source = (REPO_ROOT / "scripts" / "run-eval.sh").read_text()
    assert "from eval_harness import MONGO_COLLECTIONS" in source
    assert "for col in MONGO_COLLECTIONS" in source
    assert "for col in ['player_info'" not in source


def test_reset_player_db_targets_configured_database(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="reset_ok\n", stderr="", returncode=0)

    monkeypatch.setattr(eval_harness, "MONGO_DB", "kaetram_eval")
    monkeypatch.setattr(eval_harness.subprocess, "run", fake_run)

    assert eval_harness.reset_player_db("EvalBot") is True
    assert calls[0][4] == "kaetram_eval"
    assert '"evalbot"' in calls[0][-1]


def test_reset_player_db_reports_missing_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        eval_harness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="", stderr="", returncode=0),
    )

    assert eval_harness.reset_player_db("EvalBot") is False


def test_required_reset_aborts_on_missing_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(eval_harness, "MONGO_DB", "kaetram_eval")
    monkeypatch.setattr(eval_harness, "reset_player_db", lambda username: False)
    sleep_calls = []
    monkeypatch.setattr(eval_harness.time, "sleep", sleep_calls.append)

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        eval_harness.require_player_db_reset("EvalBot")
    assert sleep_calls == [1.0, 1.0]


def test_required_reset_retries_transient_failure(monkeypatch) -> None:
    outcomes = iter((False, True))
    monkeypatch.setattr(eval_harness, "reset_player_db", lambda username: next(outcomes))
    monkeypatch.setattr(eval_harness.time, "sleep", lambda seconds: None)

    eval_harness.require_player_db_reset("EvalBot")


def test_reset_rejects_success_marker_from_failed_command(monkeypatch) -> None:
    monkeypatch.setattr(
        eval_harness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="reset_ok\n", stderr="connection failed", returncode=2,
        ),
    )

    assert eval_harness.reset_player_db("EvalBot") is False


def test_game_database_attestation_binds_effective_dotenv_lane(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.defaults").write_text(
        "DATABASE='mongodb'\n"
        "SKIP_DATABASE=true\n"
        "MONGODB_HOST='127.0.0.1'\n"
        "MONGODB_PORT=27017\n"
        "MONGODB_DATABASE='kaetram_devlopment'\n"
        "MONGODB_TLS=false\n"
        "MONGODB_SRV=false\n"
        "MONGODB_USER=''\n"
        "MONGODB_PASSWORD=''\n"
        "MONGODB_AUTH_SOURCE=''\n"
    )
    (tmp_path / ".env").write_text(
        "DATABASE='mongodb'\n"
        "SKIP_DATABASE=false\n"
        "MONGODB_HOST='127.0.0.1'\n"
        "MONGODB_PORT=27017\n"
        "MONGODB_DATABASE='kaetram_eval'\n"
    )

    record = eval_harness.attest_game_database_configuration(
        tmp_path, "kaetram_eval", environ={}
    )

    assert record["schema"] == eval_harness.GAME_DATABASE_ATTESTATION_SCHEMA
    assert record["effective_backend"] == "mongodb"
    assert record["skip_database"] is False
    assert record["effective_database"] == "kaetram_eval"
    assert [item["path"] for item in record["config_files"]] == [
        ".env.defaults",
        ".env",
    ]
    assert len(record["attestation_sha256"]) == 64


def test_game_database_attestation_rejects_lane_mismatch_and_node_override(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.defaults").write_text(
        "DATABASE='mongodb'\n"
        "SKIP_DATABASE=false\n"
        "MONGODB_HOST='127.0.0.1'\n"
        "MONGODB_PORT=27017\n"
        "MONGODB_DATABASE='kaetram_devlopment'\n"
        "MONGODB_TLS=false\n"
        "MONGODB_SRV=false\n"
        "MONGODB_USER=''\n"
        "MONGODB_PASSWORD=''\n"
        "MONGODB_AUTH_SOURCE=''\n"
    )
    (tmp_path / ".env").write_text(
        "DATABASE='mongodb'\n"
        "SKIP_DATABASE=false\n"
        "MONGODB_HOST='127.0.0.1'\n"
        "MONGODB_PORT=27017\n"
        "MONGODB_DATABASE='kaetram_eval'\n"
    )
    with pytest.raises(RuntimeError, match="differs from harness"):
        eval_harness.attest_game_database_configuration(
            tmp_path, "other_lane", environ={}
        )

    (tmp_path / ".env.e2e").write_text("MONGODB_DATABASE='kaetram_e2e'\n")
    with pytest.raises(RuntimeError, match="differs from harness"):
        eval_harness.attest_game_database_configuration(
            tmp_path, "kaetram_eval", environ={"NODE_ENV": "e2e"}
        )

    (tmp_path / ".env.e2e").unlink()
    (tmp_path / ".env").write_text(
        "DATABASE='mongodb'\n"
        "SKIP_DATABASE=true\n"
        "MONGODB_HOST='127.0.0.1'\n"
        "MONGODB_PORT=27017\n"
        "MONGODB_DATABASE='kaetram_eval'\n"
    )
    with pytest.raises(RuntimeError, match="skip_database='true'"):
        eval_harness.attest_game_database_configuration(
            tmp_path, "kaetram_eval", environ={}
        )


def test_game_database_attestation_rejects_ambiguous_or_untracked_inputs(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.defaults").write_text(
        "DATABASE='mongodb'\n"
        "SKIP_DATABASE=false\n"
        "MONGODB_HOST='127.0.0.1'\n"
        "MONGODB_PORT=27017\n"
        "MONGODB_DATABASE='kaetram_devlopment'\n"
        "MONGODB_TLS=false\n"
        "MONGODB_SRV=false\n"
        "MONGODB_USER=''\n"
        "MONGODB_PASSWORD=''\n"
        "MONGODB_AUTH_SOURCE=''\n"
    )
    (tmp_path / ".env").write_text(
        "MONGODB_DATABASE='one'\nMONGODB_DATABASE='two'\n"
    )
    with pytest.raises(RuntimeError, match="duplicate"):
        eval_harness.attest_game_database_configuration(
            tmp_path, "two", environ={}
        )

    (tmp_path / ".env").write_text("MONGODB_DATABASE=one#dotenv-v8-keeps-this\n")
    with pytest.raises(RuntimeError, match="ambiguous or unsafe"):
        eval_harness.attest_game_database_configuration(
            tmp_path, "one", environ={}
        )

    (tmp_path / ".env").unlink()
    with pytest.raises(RuntimeError, match="requires regular"):
        eval_harness.attest_game_database_configuration(
            tmp_path, "kaetram_devlopment", environ={}
        )


@pytest.mark.parametrize(
    "environ",
    [
        {
            "DOTENV_CONFIG_INCLUDE_PROCESS_ENV": "true",
            "MONGODB_DATABASE": "wrong_lane",
        },
        {"DOTENV_CONFIG_PATH": "/tmp/untracked.env"},
        {"MONGODB_DATABASE": "wrong_lane"},
        {"SKIP_DATABASE": "false"},
    ],
)
def test_game_database_attestation_rejects_ambient_dotenv_overrides(
    tmp_path: Path,
    environ: dict[str, str],
) -> None:
    (tmp_path / ".env.defaults").write_text(
        "DATABASE='mongodb'\n"
        "SKIP_DATABASE=false\n"
        "MONGODB_HOST='127.0.0.1'\n"
        "MONGODB_PORT=27017\n"
        "MONGODB_DATABASE='kaetram_devlopment'\n"
        "MONGODB_TLS=false\n"
        "MONGODB_SRV=false\n"
        "MONGODB_USER=''\n"
        "MONGODB_PASSWORD=''\n"
        "MONGODB_AUTH_SOURCE=''\n"
    )
    (tmp_path / ".env").write_text(
        "DATABASE='mongodb'\n"
        "SKIP_DATABASE=false\n"
        "MONGODB_HOST='127.0.0.1'\n"
        "MONGODB_PORT=27017\n"
        "MONGODB_DATABASE='kaetram_eval'\n"
    )
    with pytest.raises(RuntimeError, match="ambient"):
        eval_harness.attest_game_database_configuration(
            tmp_path, "kaetram_eval", environ=environ
        )
