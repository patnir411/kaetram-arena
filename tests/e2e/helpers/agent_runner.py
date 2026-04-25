"""LLM-in-loop agent runner for quest phase tests.

Spawns arena's play_qwen.py as a subprocess, parses the resulting JSONL
session log, and evaluates a QuestSnapshot for the caller's success closure.

Design notes:
  - temperature=0 + fixed seed is the default for determinism in CI
  - NOT a full bench replacement — bench/runner.py in QwenPlays handles
    metrics, dashboards, long runs. This runner is the narrower "did the
    agent achieve the objective in N turns" test harness
  - reads authoritative state from MongoDB (not log heuristics) for the
    pass/fail check — quest stages may land in Mongo even if the log's
    tool_response parse was fuzzy
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .kaetram_world import QUESTS
from .llm_endpoint import LLMEndpoint
from .quest_phases import Phase, QuestSnapshot
from .seed import cleanup_player, seed_player, snapshot_player, summarize_snapshot

ARENA_ROOT = Path(__file__).resolve().parents[3]  # kaetram-arena/
PLAY_QWEN = ARENA_ROOT / "play_qwen.py"
MCP_SERVER = ARENA_ROOT / "mcp_game_server.py"
STATE_EXTRACTOR = ARENA_ROOT / "state_extractor.js"
PROMPTS_SYSTEM = ARENA_ROOT / "prompts" / "system.md"
PROMPTS_GAME_KNOWLEDGE = ARENA_ROOT / "prompts" / "game_knowledge.md"


@dataclass
class AgentResult:
    success: bool
    turns_played: int
    tool_calls: list[str]
    final_snapshot: QuestSnapshot
    sandbox_dir: Path
    timed_out: bool
    returncode: int

    def diagnostic(self) -> str:
        """Human-readable blob for assertion messages."""
        return (
            f"success={self.success} turns={self.turns_played} "
            f"timed_out={self.timed_out} rc={self.returncode}\n"
            f"tool_calls[:8]: {self.tool_calls[:8]}\n"
            f"final_stages: {self.final_snapshot.quest_stages}\n"
            f"final_inventory: {dict(list(self.final_snapshot.inventory_keys.items())[:8])}\n"
            f"sandbox: {self.sandbox_dir}"
        )


def _build_system_prompt(username: str, sandbox: Path) -> Path:
    """Render arena's system.md + game_knowledge.md with placeholders."""
    text = PROMPTS_SYSTEM.read_text()
    try:
        knowledge = PROMPTS_GAME_KNOWLEDGE.read_text()
    except FileNotFoundError:
        knowledge = ""
    text = (
        text.replace("__PROJECT_DIR__", str(sandbox))
            .replace("__USERNAME__", username)
            .replace("__SERVER_PORT__", "")
            .replace("__PERSONALITY_BLOCK__", "")
            .replace("__GAME_KNOWLEDGE_BLOCK__", knowledge)
    )
    tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, dir=sandbox)
    tmp.write(text)
    tmp.close()
    return Path(tmp.name)


def _snapshot_from_db(username: str, tool_calls: list[str],
                      turns_played: int) -> QuestSnapshot:
    """Build a QuestSnapshot from post-run Mongo state."""
    snap = snapshot_player(username)
    summ = summarize_snapshot(snap)
    quest_doc = (snap.get("player_quests") or {}).get("quests") or []
    stages = {}
    finished = {}
    for q in quest_doc:
        key = q.get("key")
        if not key:
            continue
        stage = int(q.get("stage", 0) or 0)
        stages[key] = stage
        stage_count = QUESTS.get(key, {}).get("stage_count", 0)
        finished[key] = stage_count > 0 and stage >= stage_count

    inv_keys: dict[str, int] = {}
    for item in summ.get("inventory") or []:
        k = item.get("key")
        if not k:
            continue
        inv_keys[k] = inv_keys.get(k, 0) + int(item.get("count", 1) or 1)

    return QuestSnapshot(
        quest_stages=stages,
        quest_finished=finished,
        inventory_keys=inv_keys,
        position=(
            (summ["position"]["x"], summ["position"]["y"])
            if summ.get("position") and summ["position"].get("x") is not None
            else None
        ),
        hit_points=summ.get("hit_points"),
        tool_calls=tool_calls,
        turns_played=turns_played,
    )


def _parse_log(log_path: Path) -> tuple[list[str], int]:
    """Extract ordered tool-call names + assistant-turn count from a
    play_qwen session JSONL."""
    tool_calls: list[str] = []
    turns = 0
    try:
        for line in log_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if rec.get("role") == "assistant":
                turns += 1
                for tc in rec.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("name"):
                        tool_calls.append(tc["name"])
    except FileNotFoundError:
        pass
    return tool_calls, turns


def run_agent_phase(
    *,
    username: str,
    phase: Phase,
    endpoint: LLMEndpoint,
    game_url: str | None = None,
    time_budget_seconds: int = 180,
    headed: bool = False,
) -> AgentResult:
    """Execute one phase end-to-end. Returns an AgentResult.

    Caller is responsible for:
      - pytest skip if endpoint is None (use llm_endpoint.skip_if_no_llm)
      - eventual cleanup_player on test teardown (done via fixture scope)
    """
    # Resolve client URL: explicit arg > KAETRAM_CLIENT_URL env (set by
    # conftest from KAETRAM_CLIENT_PORT) > localhost:9000 fallback.
    if game_url is None:
        game_url = os.environ.get("KAETRAM_CLIENT_URL", "http://localhost:9000")
    sandbox = Path(tempfile.mkdtemp(prefix=f"kaetram_quest_{phase.phase_id}_"))
    (sandbox / "state").mkdir(parents=True, exist_ok=True)
    (sandbox / "logs").mkdir(parents=True, exist_ok=True)

    # 1. Clean + seed
    cleanup_player(username)
    seed_player(username, **phase.seed)

    # 2. Build system prompt file
    system_prompt_file = _build_system_prompt(username, sandbox)

    # 3. Environment for mcp_game_server (spawned by play_qwen)
    env = {
        **os.environ,
        "KAETRAM_USERNAME": username,
        "KAETRAM_PASSWORD": "test",
        "KAETRAM_CLIENT_URL": game_url,
        "KAETRAM_EXTRACTOR": str(STATE_EXTRACTOR),
        "KAETRAM_SCREENSHOT_DIR": str(sandbox / "state"),
        "KAETRAM_HEADED": "1" if headed else "0",
    }

    # 4. Spawn play_qwen
    import sys
    cmd = [
        sys.executable, str(PLAY_QWEN),
        "--endpoint", endpoint.base_url,
        "--model", endpoint.model,
        "--system-prompt", str(system_prompt_file),
        "--user-prompt", phase.user_prompt,
        "--sandbox", str(sandbox),
        "--max-turns", str(phase.max_turns),
        "--project-dir", str(ARENA_ROOT),
    ]
    started = time.time()
    stdout_log = (sandbox / "stdout.log").open("w")
    proc = subprocess.Popen(cmd, env=env, stdout=stdout_log,
                            stderr=subprocess.STDOUT)
    timed_out = False
    try:
        proc.wait(timeout=time_budget_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    stdout_log.close()
    try:
        system_prompt_file.unlink()
    except OSError:
        pass

    # 5. Wait briefly for Kaetram autosave to flush quest state
    time.sleep(2.0)

    # 6. Parse log + snapshot Mongo
    session_logs = sorted((sandbox / "logs").glob("session_*.log"))
    tool_calls, turns = _parse_log(session_logs[-1]) if session_logs else ([], 0)

    snapshot = _snapshot_from_db(username, tool_calls, turns)
    result = AgentResult(
        success=phase.success(snapshot),
        turns_played=turns,
        tool_calls=tool_calls,
        final_snapshot=snapshot,
        sandbox_dir=sandbox,
        timed_out=timed_out,
        returncode=proc.returncode or 0,
    )
    return result


def clean_sandbox(sandbox: Path) -> None:
    """Remove a sandbox directory. Caller opts in — some tests want artifacts."""
    try:
        shutil.rmtree(sandbox)
    except OSError:
        pass
