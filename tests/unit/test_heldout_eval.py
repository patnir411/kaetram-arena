from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from eval_harness import (
    _save_results,
    _held_out_quest_metrics,
    check_scenario_success,
    resolve_system_prompt,
    run_episode,
)
from heldout_guard import (
    DEFAULT_REGISTRATION,
    HeldOutGuardError,
    assert_quests_not_reserved,
    assert_text_not_reserved,
    load_registration,
    validate_eval_selection,
)


REPO = Path(__file__).resolve().parents[2]


def test_no_game_knowledge_prompt_targets_preregistered_quest_without_walkthrough():
    prompt = resolve_system_prompt(
        str(REPO),
        "evalbot",
        include_game_knowledge=False,
        held_out_quest="Desert Quest",
    )
    knowledge = (REPO / "prompts" / "game_knowledge.md").read_text()
    assert knowledge not in prompt
    assert "__GAME_KNOWLEDGE_BLOCK__" not in prompt
    assert "your sole objective is to complete Desert Quest" in prompt
    assert "Dying Soldier" not in prompt
    assert prompt.count("Desert Quest") == 1


@pytest.mark.parametrize(
    "personality",
    ["grinder", "completionist", "explorer_tinkerer"],
)
def test_every_required_personality_passes_complete_heldout_prompt_guard(
    personality: str,
) -> None:
    registration = load_registration()
    prompt = resolve_system_prompt(
        str(REPO),
        "evalbot",
        personality,
        include_game_knowledge=False,
        held_out_quest=registration.quest_name,
        held_out_registration=registration,
    )
    objective = "your sole objective is to complete Desert Quest"
    assert objective in prompt
    outside_objective = prompt.replace(objective, "")
    assert all(
        marker.casefold() not in outside_objective.casefold()
        for marker in registration.prompt_forbidden_markers
    )


def test_resolved_prompt_guard_rejects_personality_hint(tmp_path: Path) -> None:
    (tmp_path / "prompts" / "personalities").mkdir(parents=True)
    shutil.copy(REPO / "prompts" / "system.md", tmp_path / "prompts" / "system.md")
    (tmp_path / "prompts" / "personalities" / "leaky.md").write_text(
        "Go find Wife in crullfield."
    )
    with pytest.raises(HeldOutGuardError, match="prompt leakage"):
        resolve_system_prompt(
            str(tmp_path),
            "evalbot",
            "leaky",
            include_game_knowledge=False,
            held_out_quest="Desert Quest",
        )


@pytest.mark.parametrize(
    "leak",
    [
        "Dying-Soldier",
        "crull field",
        "lakes world",
        "lava npc",
        "Ｄｅｓｅｒｔ Ｑｕｅｓｔ",
    ],
)
def test_resolved_prompt_guard_rejects_separator_variants(
    tmp_path: Path, leak: str
) -> None:
    (tmp_path / "prompts" / "personalities").mkdir(parents=True)
    shutil.copy(REPO / "prompts" / "system.md", tmp_path / "prompts" / "system.md")
    (tmp_path / "prompts" / "personalities" / "leaky.md").write_text(
        f"Seek {leak}."
    )
    with pytest.raises(HeldOutGuardError, match="prompt leakage"):
        resolve_system_prompt(
            str(tmp_path),
            "evalbot",
            "leaky",
            include_game_knowledge=False,
            held_out_quest="Desert Quest",
        )


def test_eval_episode_sets_tool_boundary_policy_env(tmp_path: Path, monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*args, **kwargs):
        captured["argv"] = args[0]
        captured.update(kwargs["env"])
        return Result()

    monkeypatch.setattr("eval_harness.subprocess.run", fake_run)
    secret = "https://signed.example.invalid/v1?token=TOP_SECRET"
    monkeypatch.setenv("KAETRAM_TEST_ENDPOINT", secret)
    run_episode(
        project_dir=str(REPO),
        endpoint=secret,
        model_api_name="2b-base",
        sandbox=str(tmp_path / "sandbox"),
        duration_seconds=1,
        system_prompt_file=str(REPO / "prompts" / "system.md"),
        username="evalbot",
        run_dir=tmp_path / "run",
        held_out_quest="Desert Quest",
        no_walkthrough=True,
        endpoint_env="KAETRAM_TEST_ENDPOINT",
    )
    assert captured["KAETRAM_NO_WALKTHROUGH"] == "1"
    assert captured["KAETRAM_HELDOUT_QUEST"] == "Desert Quest"
    assert secret not in captured["argv"]
    assert ["--endpoint-env", "KAETRAM_TEST_ENDPOINT"] == captured["argv"][-2:]


def test_results_metadata_stores_endpoint_reference_not_signed_url(tmp_path: Path, monkeypatch):
    secret = "https://signed.example.invalid/v1?token=TOP_SECRET"
    monkeypatch.setenv("KAETRAM_TOOL_SCHEMA_SOURCE", "canonical")
    path = tmp_path / "results.json"
    _save_results(path, "base", "env:KAETRAM_QWEN_2B_BASE_ENDPOINT", "D", [])
    persisted = path.read_text()
    assert "env:KAETRAM_QWEN_2B_BASE_ENDPOINT" in persisted
    assert secret not in persisted
    assert "TOP_SECRET" not in persisted
    assert '"tool_schema_source": "canonical"' in persisted


def test_results_metadata_records_loaded_heldout_digest(tmp_path: Path) -> None:
    registration = load_registration()
    path = tmp_path / "results.json"
    result = _save_results(
        path,
        "base",
        "env:MODEL_ENDPOINT",
        "D",
        [],
        include_game_knowledge=False,
        held_out_registration=registration,
    )
    assert result["meta"]["held_out_registration_sha256"] == registration.sha256
    assert result["meta"]["held_out_registration"] == str(registration.path)


def test_default_registration_is_locked_and_eval_only():
    registration = load_registration()
    assert registration.quest_name == "Desert Quest"
    assert registration.allowed_uses == {"evaluation"}
    assert {"training_seed", "teacher_grading"}.issubset(registration.forbidden_uses)
    assert validate_eval_selection("desertquest") == registration
    assert registration.prompt_forbidden_markers
    assert registration.forbidden_token_sequences
    assert registration.tokenizer_vocab_size == 248077


def test_registration_mismatch_and_unlocked_copy_fail_closed(tmp_path: Path):
    with pytest.raises(HeldOutGuardError, match="does not match"):
        validate_eval_selection("Foresting")

    raw = json.loads(DEFAULT_REGISTRATION.read_text())
    raw["locked"] = False
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(HeldOutGuardError, match="locked=true"):
        load_registration(path)
    with pytest.raises(HeldOutGuardError, match="schema_version 2"):
        validate_eval_selection(
            "Desert Quest",
            REPO / "research" / "experiments" / "heldout-quest.json",
        )


@pytest.mark.parametrize(
    ("digest", "message"),
    [
        ("", "requires --held-out-registration-sha256"),
        ("0" * 64, "registration SHA-256 mismatch"),
    ],
)
def test_cli_requires_exact_heldout_registration_digest(
    tmp_path: Path, digest: str, message: str
) -> None:
    command = [
        sys.executable,
        str(REPO / "eval_harness.py"),
        "--models",
        "test=http://127.0.0.1:1/v1",
        "--episodes",
        "1",
        "--scenario",
        "D",
        "--output-dir",
        str(tmp_path / "out"),
        "--omit-game-knowledge",
        "--held-out-quest",
        "Desert Quest",
        "--held-out-registration",
        str(DEFAULT_REGISTRATION),
    ]
    if digest:
        command.extend(["--held-out-registration-sha256", digest])
    result = subprocess.run(
        command,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert message in result.stderr


def test_seed_and_teacher_grading_guards_block_aliases():
    with pytest.raises(HeldOutGuardError, match="training_seed"):
        assert_quests_not_reserved(["foresting", "desertquest"], use="training_seed")
    with pytest.raises(HeldOutGuardError, match="teacher_grading"):
        assert_text_not_reserved(
            "interact_npc targeted Dying Soldier",
            use="teacher_grading",
            source="session.log",
        )
    assert_quests_not_reserved(["foresting", "ricksroll"], use="training_seed")


def test_held_out_metrics_use_alias_normalization_and_override_scenario_success():
    registration = load_registration()
    before = {"quests": {"desertquest": {"stage": 1, "finished": False}}}
    after = {"quests": {"Desert Quest": {"stage": 3, "finished": True}}}
    metrics = _held_out_quest_metrics(before, after, registration)
    assert metrics["held_out_quest_stages_advanced"] == 2
    assert metrics["held_out_quest_completed_delta"] == 1
    assert check_scenario_success("D", metrics, "Desert Quest")


def test_held_out_metrics_treat_explicitly_null_quest_maps_as_empty():
    registration = load_registration()
    metrics = _held_out_quest_metrics({"quests": None}, {"quests": None}, registration)
    assert metrics["held_out_quest_stages_advanced"] == 0
    assert metrics["held_out_quest_completed_delta"] == 0


def test_held_out_success_requires_registered_quest_completion():
    metrics = {
        "turns_played": 100,
        "tool_parse_rate": 1.0,
        "held_out_quest_completed_delta": 0,
    }
    assert check_scenario_success("D", metrics)
    assert not check_scenario_success("D", metrics, "Desert Quest")
