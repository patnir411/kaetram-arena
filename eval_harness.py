#!/usr/bin/env python3
"""
eval_harness.py — Standardized evaluation harness for Kaetram AI agents.

Runs N episodes per model with controlled conditions:
1. Resets MongoDB player data between episodes (fresh Level 1)
2. Runs play_qwen.py with fixed max turns
3. Parses session logs for per-episode metrics
4. Outputs aggregated results JSON for eval_compare.py

Usage:
    # Default: 30 episodes, scenario D (open play), both models
    python3 eval_harness.py --episodes 30

    # Specific scenario
    python3 eval_harness.py --episodes 50 --scenario A

    # Custom endpoints
    python3 eval_harness.py \
        --models base=https://...base.../v1 r9-sft=https://...serve.../v1 \
        --episodes 30 --scenario D

    # Single model
    python3 eval_harness.py --models r9-sft=https://...serve.../v1 --episodes 10

Requires: game server running on --server-port, MongoDB in Docker (kaetram-mongo).
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default Modal endpoints with per-model config
# Each model gets its own username (no hyphens — Kaetram rejects them) and game server port.
DEFAULT_MODELS = {
    "base": {
        "endpoint": "https://patnir411--kaetram-qwen-base-inference-serve.modal.run/v1",
        "username": "evalbotBase",
        "server_port": "9071",
    },
    "r9-sft": {
        "endpoint": "https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1",
        "username": "evalbotR9",
        "server_port": "9061",
    },
}

# Evaluation scenarios from KAE-34 / reference/EVALS.md
SCENARIOS = {
    "A": {
        "name": "Rat Grind",
        "max_turns": 100,
        "description": "Kill 10 rats from Level 1 in Mudwich",
    },
    "B": {
        "name": "Snek Quest",
        "max_turns": 200,
        "description": "Complete Bike Lyson snake quest",
    },
    "C": {
        "name": "Multi-Zone",
        "max_turns": 150,
        "description": "Visit 3+ zones via warping",
    },
    "D": {
        "name": "Open Play",
        "max_turns": 300,
        "description": "300 turns open-ended from Level 1",
    },
}

MONGO_CONTAINER = "kaetram-mongo"
MONGO_DB = "kaetram_devlopment"
MONGO_COLLECTIONS = [
    "player_info", "player_skills", "player_equipment",
    "player_inventory", "player_bank", "player_quests",
    "player_achievements", "player_statistics", "player_abilities",
]


# ---------------------------------------------------------------------------
# MongoDB reset
# ---------------------------------------------------------------------------

def reset_player_db(username: str) -> bool:
    """Delete all MongoDB records for a specific player username."""
    # Kaetram stores usernames lowercase
    username_lower = username.lower()
    js_parts = [
        f"db.{c}.deleteMany({{username: '{username_lower}'}})"
        for c in MONGO_COLLECTIONS
    ]
    js = "; ".join(js_parts) + "; print('reset_ok');"
    try:
        result = subprocess.run(
            ["docker", "exec", MONGO_CONTAINER, "mongosh", MONGO_DB,
             "--quiet", "--eval", js],
            capture_output=True, text=True, timeout=15,
        )
        return "reset_ok" in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  Warning: MongoDB reset failed: {e}")
        return False


# ---------------------------------------------------------------------------
# System prompt resolution
# ---------------------------------------------------------------------------

def resolve_system_prompt(project_dir: str, username: str, personality: str = "") -> str:
    """Resolve system.md template with game knowledge and optional personality."""
    system_path = os.path.join(project_dir, "prompts", "system.md")
    knowledge_path = os.path.join(project_dir, "prompts", "game_knowledge.md")

    with open(system_path) as f:
        prompt = f.read()
    knowledge = ""
    if os.path.isfile(knowledge_path):
        with open(knowledge_path) as f:
            knowledge = f.read()

    # Load personality block if specified
    personality_block = ""
    if personality:
        pers_path = os.path.join(project_dir, "prompts", "personalities", f"{personality}.md")
        if os.path.isfile(pers_path):
            with open(pers_path) as f:
                personality_block = f.read()
            print(f"  Personality: {personality} ({len(personality_block)} chars)")
        else:
            print(f"  WARNING: personality file not found: {pers_path}")

    prompt = prompt.replace("__USERNAME__", username)
    prompt = prompt.replace("__GAME_KNOWLEDGE_BLOCK__", knowledge)
    prompt = prompt.replace("__PERSONALITY_BLOCK__", personality_block)
    prompt = prompt.replace("__PROJECT_DIR__", project_dir)
    prompt = prompt.replace("__SERVER_PORT__", "")
    return prompt


# ---------------------------------------------------------------------------
# Episode runner
# ---------------------------------------------------------------------------

def run_episode(
    project_dir: str,
    endpoint: str,
    model_api_name: str,
    sandbox: str,
    max_turns: int,
    system_prompt_file: str,
    username: str,
    server_port: str = "",
) -> dict:
    """Run one play_qwen.py episode as subprocess. Returns run metadata."""
    cmd = [
        sys.executable, os.path.join(project_dir, "play_qwen.py"),
        "--endpoint", endpoint,
        "--model", model_api_name,
        "--sandbox", sandbox,
        "--max-turns", str(max_turns),
        "--system-prompt", system_prompt_file,
        "--project-dir", project_dir,
    ]
    if server_port:
        cmd.extend(["--server-port", server_port])

    env = {**os.environ, "KAETRAM_USERNAME": username, "PYTHONUNBUFFERED": "1"}

    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=max(max_turns * 30, 3600),  # generous timeout
            env=env,
        )
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        returncode = -1
        result = type("R", (), {"stdout": "", "stderr": "TIMEOUT"})()
    duration = time.time() - start

    # Save full stdout/stderr for debugging
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if stdout or stderr:
        debug_dir = Path(sandbox) / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        if stdout:
            (debug_dir / "stdout.log").write_text(stdout)
        if stderr:
            (debug_dir / "stderr.log").write_text(stderr)

    return {
        "returncode": returncode,
        "duration_seconds": round(duration, 1),
        "stdout_tail": stdout[-1000:],
        "stderr_tail": stderr[-500:],
    }


def find_latest_log(sandbox: str) -> Path | None:
    """Find the most recently created session log in the sandbox."""
    log_dir = Path(sandbox) / "logs"
    if not log_dir.is_dir():
        return None
    logs = sorted(log_dir.glob("session_*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def find_newest_new_log(sandbox: str, known_logs: set[str]) -> Path | None:
    """Find the newest session log created after a sub-session starts."""
    log_dir = Path(sandbox) / "logs"
    if not log_dir.is_dir():
        return None
    new_logs = [p for p in log_dir.glob("session_*.log") if p.name not in known_logs]
    if not new_logs:
        return None
    return max(new_logs, key=lambda p: p.stat().st_mtime)


def start_eval_watchdog(
    project_dir: str,
    output_dir: Path,
    models: dict[str, dict],
    episodes: int,
    interval: int,
    stale_seconds: int,
    kill_on_failure: bool,
) -> tuple[subprocess.Popen | None, object | None]:
    """Launch the standalone watchdog as a sibling background process."""
    script_path = Path(project_dir) / "scripts" / "eval_watchdog.py"
    if not script_path.is_file():
        print(f"  WARNING: watchdog script missing: {script_path}")
        return None, None

    log_path = Path("/tmp") / f"eval_watchdog_{output_dir.name or 'eval'}.log"
    cmd = [
        sys.executable,
        str(script_path),
        "--run-dir", str(output_dir),
        "--episodes", str(episodes),
        "--interval", str(interval),
        "--stale-seconds", str(stale_seconds),
    ]
    if kill_on_failure:
        cmd.append("--kill-on-failure")
    for model_name, model_cfg in models.items():
        sandbox = f"/tmp/kaetram_eval_{model_name}"
        cmd.extend([
            "--model",
            f"{model_name}={model_cfg['endpoint']},{sandbox},{model_cfg['server_port']}",
        ])

    log_f = open(log_path, "w")
    proc = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT)
    print(f"  Watchdog: pid={proc.pid} log={log_path}")
    return proc, log_f


# ---------------------------------------------------------------------------
# Log parsing & metrics
# ---------------------------------------------------------------------------

def parse_log(log_path: Path) -> list[dict]:
    """Parse play_qwen.py JSONL log into list of entries."""
    entries = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries



def _entropy(counts: Counter) -> float:
    """Shannon entropy of a Counter in bits."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return -sum(
        (c / total) * math.log2(c / total)
        for c in counts.values() if c > 0
    )


def _parse_tool_json(content: str) -> dict | None:
    """Try to parse JSON from a tool result string like 'tool_name: {...}'."""
    if ": " in content:
        json_str = content.split(": ", 1)[1]
    else:
        json_str = content
    # observe results append ASCII_MAP after the JSON — strip it before parsing
    if "\n\nASCII_MAP:" in json_str:
        json_str = json_str.split("\n\nASCII_MAP:")[0]
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None


def _read_player_db_snapshot(username: str) -> dict | None:
    """Read authoritative MongoDB metrics for one player."""
    try:
        from pymongo import MongoClient
        from dashboard.db import COMBAT_SKILL_TYPES, _exp_to_level
    except ImportError:
        return None

    client = MongoClient("localhost", 27017, serverSelectionTimeoutMS=2000)
    try:
        db = client[MONGO_DB]
        username_lower = username.lower()

        stats_doc = db["player_statistics"].find_one({"username": username_lower}, {"_id": 0, "mobKills": 1})
        mob_kills = stats_doc.get("mobKills", {}) if stats_doc else {}
        kills_total = sum(v for v in mob_kills.values() if isinstance(v, (int, float)))

        skills_doc = db["player_skills"].find_one({"username": username_lower}, {"_id": 0, "skills": 1})
        total_xp = 0
        skill_levels = {}
        if skills_doc and isinstance(skills_doc.get("skills"), list):
            for skill in skills_doc["skills"]:
                exp = int(skill.get("experience", 0) or 0)
                skill_type = skill.get("type", -1)
                total_xp += exp
                skill_levels[skill_type] = _exp_to_level(exp)

        combat_level = 1
        for skill_type in COMBAT_SKILL_TYPES:
            combat_level += skill_levels.get(skill_type, 1) - 1

        return {
            "kills_total": int(kills_total),
            "xp_total": int(total_xp),
            "level": int(combat_level),
        }
    except Exception:
        return None
    finally:
        client.close()


def _diff_player_db_metrics(before: dict | None, after: dict | None) -> dict:
    """Compute delta metrics from MongoDB snapshots."""
    before = before or {"kills_total": 0, "xp_total": 0, "level": 1}
    after = after or {"kills_total": 0, "xp_total": 0, "level": 1}
    return {
        "kills_db": after["kills_total"],
        "kills_db_delta": max(0, after["kills_total"] - before["kills_total"]),
        "xp_db": after["xp_total"],
        "xp_db_delta": max(0, after["xp_total"] - before["xp_total"]),
        "level_reached_db": after["level"],
        "level_delta_db": after["level"] - before["level"],
    }


def _read_player_db_snapshot_with_retry(username: str, retries: int = 5, delay: float = 1.0) -> dict | None:
    """Retry MongoDB reads briefly to allow autosave/logout persistence."""
    for attempt in range(retries):
        snapshot = _read_player_db_snapshot(username)
        if snapshot is not None:
            return snapshot
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def _read_quest_achievement_snapshot(username: str) -> dict | None:
    """Read DB-authoritative quest & achievement state for one player.

    Delegates parsing to dashboard.db (shared with the live dashboard), then
    reshapes list-of-dicts → key-indexed map for cheap episode diffing.
    """
    try:
        from pymongo import MongoClient
        from dashboard.db import summarize_quest_doc, summarize_achievement_doc
    except ImportError:
        return None

    client = MongoClient("localhost", 27017, serverSelectionTimeoutMS=2000)
    try:
        db = client[MONGO_DB]
        username_lower = username.lower()

        quest_doc = db["player_quests"].find_one({"username": username_lower}, {"_id": 0, "quests": 1})
        quests_list, _ = summarize_quest_doc(quest_doc)
        quests = {q["key"]: {
            "stage": q["stage"],
            "stage_count": q["stageCount"],
            "started": q["started"],
            "finished": q["finished"],
        } for q in quests_list if q.get("key")}

        ach_doc = db["player_achievements"].find_one({"username": username_lower}, {"_id": 0, "achievements": 1})
        ach_list, _ = summarize_achievement_doc(ach_doc)
        achievements = {a["key"]: {
            "stage": a["stage"],
            "stage_count": a["stageCount"],
            "started": a["started"],
            "finished": a["finished"],
        } for a in ach_list if a.get("key")}

        return {"quests": quests, "achievements": achievements}
    except Exception:
        return None
    finally:
        client.close()


def _read_quest_achievement_snapshot_with_retry(username: str, retries: int = 5, delay: float = 1.0) -> dict | None:
    """Retry quest/achievement reads briefly to allow autosave/logout persistence."""
    for attempt in range(retries):
        snapshot = _read_quest_achievement_snapshot(username)
        if snapshot is not None:
            return snapshot
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def _diff_quest_achievement_metrics(before: dict | None, after: dict | None) -> dict:
    """Compute episode delta + cumulative metrics from quest/achievement snapshots."""
    before = before or {"quests": {}, "achievements": {}}
    after = after or {"quests": {}, "achievements": {}}

    def _summarize(before_map: dict, after_map: dict) -> tuple[int, int, int, int, int]:
        completed_cum = sum(1 for v in after_map.values() if v.get("finished"))
        started_cum = sum(1 for v in after_map.values() if v.get("started"))
        completed_delta = 0
        started_delta = 0
        stages_advanced = 0
        for key, after_entry in after_map.items():
            before_entry = before_map.get(key, {"stage": 0, "started": False, "finished": False})
            if after_entry.get("finished") and not before_entry.get("finished"):
                completed_delta += 1
            if after_entry.get("started") and not before_entry.get("started"):
                started_delta += 1
            stages_advanced += max(0, int(after_entry.get("stage", 0)) - int(before_entry.get("stage", 0)))
        return completed_cum, started_cum, completed_delta, started_delta, stages_advanced

    q_done, q_started, q_done_delta, q_started_delta, q_stages = _summarize(before["quests"], after["quests"])
    a_done, a_started, a_done_delta, a_started_delta, a_stages = _summarize(before["achievements"], after["achievements"])

    return {
        "quests_completed_db": q_done,
        "quests_accepted_db": q_started,
        "quests_completed_delta": q_done_delta,
        "quests_accepted_delta": q_started_delta,
        "quest_stages_advanced": q_stages,
        "achievements_completed_db": a_done,
        "achievements_started_db": a_started,
        "achievements_completed_delta": a_done_delta,
        "achievements_started_delta": a_started_delta,
        "achievement_stages_advanced": a_stages,
    }


# Known XP values per mob type (from game_knowledge.md)
MOB_XP = {
    "Rat": 18, "Batterfly": 50, "Goblin": 72, "Snek": 80,
    "Crab": 90, "Skeleton": 100, "Ogre": 120, "Zombie": 130,
    "Piranha": 110, "Spooky Skeleton": 140, "Desert Scorpion": 124,
}


def compute_episode_metrics(
    log_entries: list[dict],
    db_before: dict | None = None,
    db_after: dict | None = None,
    qa_before: dict | None = None,
    qa_after: dict | None = None,
) -> dict:
    """Compute per-episode metrics from parsed log entries.

    All metrics are derived from log entries (tool call results) rather than
    game_state.json snapshots, which are often stale or missing.
    """
    assistant_turns = 0
    tool_calls_valid = 0
    action_counts = Counter()
    deaths = 0
    stuck_resets = 0
    click_tiles = 0

    # Log-derived metrics
    kills = 0
    kills_by_mob = Counter()
    xp_estimated = 0
    max_level = 1
    max_hp = 69  # default Level 1
    positions = set()
    quests_completed_set = set()
    quests_accepted_set = set()

    for entry in log_entries:
        role = entry.get("role", "")
        content = entry.get("content", "")

        if role == "assistant":
            assistant_turns += 1
            tc_list = entry.get("tool_calls", [])
            if tc_list:
                tool_calls_valid += 1
                for tc in tc_list:
                    name = tc.get("name", "unknown")
                    action_counts[name] += 1
                    if name == "respawn":
                        deaths += 1
                    elif name == "stuck_reset":
                        stuck_resets += 1
                    elif name == "click_tile":
                        click_tiles += 1

        elif role == "tool":
            parsed = _parse_tool_json(content)
            if not parsed:
                continue

            # --- Attack results: kills, HP, positions ---
            post = parsed.get("post_attack", {})
            has_error = "error" in parsed
            if post.get("killed") and not has_error:
                mob_name = parsed.get("attacking", "Unknown")
                kills += 1
                kills_by_mob[mob_name] += 1
                xp_estimated += MOB_XP.get(mob_name, 30)
            # Track player position + max HP from attack results
            ppos = parsed.get("player_pos", {})
            if ppos.get("x") and ppos.get("y"):
                positions.add((ppos["x"], ppos["y"]))
            p_max_hp = post.get("player_max_hp", 0)
            if p_max_hp > max_hp:
                max_hp = p_max_hp

            # --- Observe results: level, quests, position ---
            if content.startswith("observe:"):
                ps = parsed.get("player_stats", {})
                if isinstance(ps, dict):
                    lvl = int(ps.get("level", 1) or 1)
                    if lvl > max_level:
                        max_level = lvl
                pp = parsed.get("player_position", {})
                if pp.get("x") and pp.get("y"):
                    positions.add((pp["x"], pp["y"]))
                # Quest tracking from observe
                obs_quests = parsed.get("quests", [])
                if isinstance(obs_quests, list):
                    for q in obs_quests:
                        if isinstance(q, dict):
                            qkey = q.get("key", q.get("name", ""))
                            stage = q.get("stage", 0)
                            if stage > 0 and qkey:
                                quests_accepted_set.add(qkey)
                            if stage == 9999 or q.get("finished") or q.get("completed"):
                                if qkey:
                                    quests_completed_set.add(qkey)
                elif isinstance(obs_quests, dict):
                    for qkey, qdata in obs_quests.items():
                        if isinstance(qdata, dict):
                            stage = qdata.get("stage", 0)
                            if stage > 0:
                                quests_accepted_set.add(qkey)
                            if stage == 9999 or qdata.get("finished") or qdata.get("completed"):
                                quests_completed_set.add(qkey)

            # --- Navigate / move results: position ---
            if content.startswith("navigate:") or content.startswith("move:"):
                ppos = parsed.get("player_pos", {})
                if ppos.get("x") and ppos.get("y"):
                    positions.add((ppos["x"], ppos["y"]))

            # --- Interact NPC: quest acceptance from dialogue ---
            if content.startswith("interact_npc:"):
                if parsed.get("quest_opened") or parsed.get("quest_started"):
                    qname = parsed.get("quest_name", parsed.get("npc", ""))
                    if qname:
                        quests_accepted_set.add(qname)

    turns_played = assistant_turns
    tool_parse_rate = tool_calls_valid / max(1, assistant_turns)
    level_delta = max_level - 1
    xp_per_turn = xp_estimated / max(1, turns_played)

    metrics = {
        "turns_played": turns_played,
        "tool_calls_attempted": assistant_turns,
        "tool_calls_valid": tool_calls_valid,
        "tool_parse_rate": round(tool_parse_rate, 4),
        "kills": kills,
        "kills_by_mob": dict(kills_by_mob),
        "xp_estimated": xp_estimated,
        "xp_per_turn": round(xp_per_turn, 4),
        "level_reached": max_level,
        "level_delta": level_delta,
        "deaths": deaths,
        "survived": deaths == 0,
        "quests_completed": len(quests_completed_set),
        "quests_accepted": len(quests_accepted_set),
        "unique_positions": len(positions),
        "action_counts": dict(action_counts),
        "action_entropy": round(_entropy(action_counts), 4),
        "stuck_resets": stuck_resets,
        "click_tiles": click_tiles,
    }
    metrics.update(_diff_player_db_metrics(db_before, db_after))
    metrics.update(_diff_quest_achievement_metrics(qa_before, qa_after))
    return metrics


# ---------------------------------------------------------------------------
# Scenario success criteria
# ---------------------------------------------------------------------------

def check_scenario_success(scenario: str, metrics: dict) -> bool:
    """Check if an episode met the scenario-specific success criteria."""
    if scenario == "A":
        # Rat Grind: killed at least 5 rats
        return metrics["kills"] >= 5 and metrics["action_counts"].get("attack", 0) >= 5
    elif scenario == "B":
        # Snek Quest: completed at least one quest
        return metrics["quests_completed"] >= 1
    elif scenario == "C":
        # Multi-Zone: used warp to visit multiple zones
        return metrics["action_counts"].get("warp", 0) >= 2
    elif scenario == "D":
        # Open Play: no fixed criteria — just played
        return metrics["turns_played"] > 10 and metrics["tool_parse_rate"] > 0.5
    return False


# ---------------------------------------------------------------------------
# Main eval orchestrator
# ---------------------------------------------------------------------------

def run_model_eval(
    model_name: str,
    endpoint: str,
    n_episodes: int,
    scenario: str,
    output_dir: Path,
    project_dir: str,
    username: str,
    server_port: str,
    resume_from: int = 0,
    personality: str = "",
) -> dict:
    """Run all episodes for one model. Returns full results dict."""
    scenario_cfg = SCENARIOS[scenario]
    max_turns = scenario_cfg["max_turns"]
    sandbox = f"/tmp/kaetram_eval_{model_name}"
    model_output_dir = output_dir / model_name
    model_output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve system prompt once, write to temp file
    prompt_text = resolve_system_prompt(project_dir, username, personality)
    prompt_file = model_output_dir / "system_prompt.md"
    prompt_file.write_text(prompt_text)

    # Model API name (what the endpoint expects)
    api_name = "kaetram" if "serve" in endpoint else "kaetram-base"

    print(f"\n{'='*60}")
    print(f"Evaluating: {model_name}")
    print(f"  Endpoint:  {endpoint}")
    print(f"  Scenario:  {scenario} — {scenario_cfg['name']} ({max_turns} turns)")
    print(f"  Episodes:  {n_episodes} (resuming from {resume_from})")
    print(f"  Sandbox:   {sandbox}")
    print(f"  Username:  {username}")
    print(f"  Port:      {server_port}")
    print(f"{'='*60}\n")

    # Ensure game server is running on the required port
    # Uses direct node command (same as orchestrate.py / start-qwen.sh)
    _game_server_proc = None
    if server_port:
        import shutil
        check_cmd = f"ss -tlnp 2>/dev/null | grep -q ':{server_port} '"
        if subprocess.run(check_cmd, shell=True).returncode != 0:
            nvm_sh = os.path.expanduser("~/.nvm/nvm.sh")
            server_dir = os.path.expanduser("~/projects/Kaetram-Open/packages/server")
            if os.path.isdir(server_dir):
                print(f"  Starting game server on port {server_port}...")
                gs_cmd = f'source "{nvm_sh}" && nvm use 20 --silent && exec node --enable-source-maps dist/main.js --port {server_port}'
                gs_log = open(f"/tmp/eval_gameserver_{server_port}.log", "w")
                _game_server_proc = subprocess.Popen(
                    ["bash", "-c", gs_cmd], cwd=server_dir,
                    stdout=gs_log, stderr=gs_log,
                    env={**os.environ, "ACCEPT_LICENSE": "true", "SKIP_DATABASE": "false"},
                )
                # Wait for port
                for _i in range(60):
                    if subprocess.run(check_cmd, shell=True).returncode == 0:
                        print(f"  Game server ready on port {server_port} ({_i+1}s)")
                        # Listening is not enough; give the world a few seconds to finish booting.
                        time.sleep(5)
                        break
                    time.sleep(1)
                else:
                    print(f"  WARNING: Game server on port {server_port} not detected after 60s")

    episodes = []

    # Load existing results if resuming
    results_path = model_output_dir / "results.json"
    if resume_from > 0 and results_path.is_file():
        with open(results_path) as f:
            existing = json.load(f)
        episodes = existing.get("episodes", [])
        print(f"  Loaded {len(episodes)} existing episodes")

    for ep_num in range(resume_from + 1, n_episodes + 1):
        print(f"\n--- Episode {ep_num}/{n_episodes} ---")

        # 1. Reset player data
        print(f"  Resetting MongoDB for {username}...")
        if not reset_player_db(username):
            print(f"  Warning: DB reset may have failed, continuing anyway")
        db_before = _read_player_db_snapshot(username)
        qa_before = _read_quest_achievement_snapshot(username)

        # Clear sandbox state (keep live_screen.jpg and mcp_server.log for dashboard)
        state_dir = Path(sandbox) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        for f in state_dir.glob("*"):
            if f.is_file() and f.name not in ("live_screen.jpg", "mcp_server.log"):
                f.unlink()

        # Clear sub-session logs from previous episode
        log_dir = Path(sandbox) / "logs"
        if log_dir.is_dir():
            for f in log_dir.glob("session_*.log"):
                f.unlink()

        # 2. Run episode with sub-session continuation
        # The model's context window fills up after ~30 turns, so play_qwen.py
        # exits. We restart it (same DB state, player continues) until we reach
        # max_turns total across all sub-sessions.
        all_log_entries = []
        total_duration = 0.0
        sub_session = 0
        last_returncode = 0

        while len([e for e in all_log_entries if e.get("role") == "assistant"]) < max_turns:
            turns_so_far = len([e for e in all_log_entries if e.get("role") == "assistant"])
            remaining = max_turns - turns_so_far
            if remaining <= 0:
                break

            sub_session += 1
            print(f"  Sub-session {sub_session}: {turns_so_far}/{max_turns} turns so far, {remaining} remaining...")
            known_logs = {p.name for p in log_dir.glob("session_*.log")} if log_dir.is_dir() else set()

            run_info = run_episode(
                project_dir=project_dir,
                endpoint=endpoint,
                model_api_name=api_name,
                sandbox=sandbox,
                max_turns=remaining,
                system_prompt_file=str(prompt_file),
                username=username,
                server_port=server_port,
            )
            total_duration += run_info["duration_seconds"]
            last_returncode = run_info["returncode"]

            # Only accept logs created by this sub-session. Reusing the previous
            # session's log would fabricate progress after a failed restart.
            log_path = find_newest_new_log(sandbox, known_logs)
            if log_path is None:
                print(f"  Sub-session {sub_session}: no log file — stopping")
                break

            sub_entries = parse_log(log_path)
            sub_turns = len([e for e in sub_entries if e.get("role") == "assistant"])
            print(f"  Sub-session {sub_session}: {sub_turns} turns ({run_info['duration_seconds']:.0f}s)")

            if sub_turns == 0:
                # Session failed to produce any turns — stop to avoid infinite loop
                print(f"  Sub-session {sub_session}: 0 turns produced, stopping episode")
                break

            all_log_entries.extend(sub_entries)

        # 3. Parse aggregated results from all sub-sessions
        total_turns = len([e for e in all_log_entries if e.get("role") == "assistant"])
        if total_turns == 0:
            print(f"  No turns produced across {sub_session} sub-sessions — episode failed")
            episode = {
                "episode": ep_num,
                "status": "no_log",
                "duration_seconds": total_duration,
                "returncode": last_returncode,
            }
            episodes.append(episode)
            _save_results(results_path, model_name, endpoint, scenario, episodes)
            print("  Aborting remaining episodes after zero-turn failure to avoid contaminating the run")
            break

        # Save combined log to eval output directory
        dest_log = model_output_dir / f"episode_{ep_num:03d}.jsonl"
        with open(dest_log, "w") as f:
            for entry in all_log_entries:
                f.write(json.dumps(entry) + "\n")

        db_after = _read_player_db_snapshot_with_retry(username)
        qa_after = _read_quest_achievement_snapshot_with_retry(username)
        metrics = compute_episode_metrics(
            all_log_entries,
            db_before=db_before, db_after=db_after,
            qa_before=qa_before, qa_after=qa_after,
        )
        success = check_scenario_success(scenario, metrics)

        episode = {
            "episode": ep_num,
            "status": "ok",
            "success": success,
            "duration_seconds": total_duration,
            "returncode": last_returncode,
            "sub_sessions": sub_session,
            "log_file": str(dest_log),
            **metrics,
        }
        episodes.append(episode)

        # Progress summary
        print(f"  Done: {metrics['turns_played']} turns ({sub_session} sub-sessions), "
              f"TPR={metrics['tool_parse_rate']:.2f}, "
              f"kills={metrics['kills']}, XP~{metrics['xp_estimated']}, "
              f"level={metrics['level_reached']}, "
              f"deaths={metrics['deaths']}, "
              f"quests={metrics.get('quests_completed_delta', metrics.get('quests_completed', 0))}"
              f"/{metrics.get('quests_accepted_delta', metrics.get('quests_accepted', 0))} "
              f"(stages+{metrics.get('quest_stages_advanced', 0)}), "
              f"ach={metrics.get('achievements_completed_delta', 0)} "
              f"(stages+{metrics.get('achievement_stages_advanced', 0)}), "
              f"{'SUCCESS' if success else 'no-success'} "
              f"({total_duration:.0f}s)")

        # 4. Save intermediate results (crash-safe)
        _save_results(results_path, model_name, endpoint, scenario, episodes)

    # Clean up game server if we started one
    if _game_server_proc and _game_server_proc.poll() is None:
        print(f"  Stopping game server on port {server_port}...")
        _game_server_proc.terminate()
        try:
            _game_server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _game_server_proc.kill()

    # Final save
    results = _save_results(results_path, model_name, endpoint, scenario, episodes)
    return results


def _save_results(path: Path, model_name: str, endpoint: str, scenario: str,
                  episodes: list[dict]) -> dict:
    """Save results JSON with metadata and aggregated metrics."""
    # Aggregate per-metric arrays for eval_compare.py
    ok_episodes = [e for e in episodes if e.get("status") == "ok"]
    metrics = {}
    if ok_episodes:
        def _quest_done(e: dict) -> int:
            # Prefer DB-authoritative episode delta; fall back to log-parsed for old runs.
            if "quests_completed_delta" in e:
                return 1 if e["quests_completed_delta"] > 0 else 0
            return 1 if e.get("quests_completed", 0) > 0 else 0

        metrics = {
            "quest_completion_rate": [_quest_done(e) for e in ok_episodes],
            "xp_per_turn": [e.get("xp_per_turn", 0) for e in ok_episodes],
            "survival_rate": [1 if e.get("survived", False) else 0 for e in ok_episodes],
            "tool_parse_rate": [e.get("tool_parse_rate", 0) for e in ok_episodes],
            "deaths_per_session": [e.get("deaths", 0) for e in ok_episodes],
            # Tier 2
            "kills": [e.get("kills", 0) for e in ok_episodes],
            "kills_db_delta": [e.get("kills_db_delta", 0) for e in ok_episodes],
            "xp_estimated": [e.get("xp_estimated", 0) for e in ok_episodes],
            "xp_db_delta": [e.get("xp_db_delta", 0) for e in ok_episodes],
            "level_reached": [e.get("level_reached", 1) for e in ok_episodes],
            "level_delta": [e.get("level_delta", 0) for e in ok_episodes],
            "level_reached_db": [e.get("level_reached_db", 1) for e in ok_episodes],
            "level_delta_db": [e.get("level_delta_db", 0) for e in ok_episodes],
            "quests_completed_delta": [e.get("quests_completed_delta", 0) for e in ok_episodes],
            "quests_accepted_delta": [e.get("quests_accepted_delta", 0) for e in ok_episodes],
            "quest_stages_advanced": [e.get("quest_stages_advanced", 0) for e in ok_episodes],
            "achievements_completed_delta": [e.get("achievements_completed_delta", 0) for e in ok_episodes],
            "achievements_started_delta": [e.get("achievements_started_delta", 0) for e in ok_episodes],
            "achievement_stages_advanced": [e.get("achievement_stages_advanced", 0) for e in ok_episodes],
            "action_entropy": [e.get("action_entropy", 0) for e in ok_episodes],
            "stuck_resets": [e.get("stuck_resets", 0) for e in ok_episodes],
            "click_tiles": [e.get("click_tiles", 0) for e in ok_episodes],
            "success_rate": [1 if e.get("success", False) else 0 for e in ok_episodes],
        }

    git_sha = ""
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        pass

    results = {
        "meta": {
            "model": model_name,
            "endpoint": endpoint,
            "scenario": scenario,
            "scenario_name": SCENARIOS[scenario]["name"],
            "max_turns": SCENARIOS[scenario]["max_turns"],
            "total_episodes": len(episodes),
            "ok_episodes": len(ok_episodes),
            "timestamp": datetime.now().isoformat(),
            "git_sha": git_sha,
        },
        "episodes": episodes,
        "metrics": metrics,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Results saved: {path}")
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Standardized evaluation harness for Kaetram AI agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 eval_harness.py --episodes 30
  python3 eval_harness.py --episodes 50 --scenario A
  python3 eval_harness.py --models r9-sft=https://your-endpoint/v1 --episodes 10
        """,
    )
    parser.add_argument(
        "--models", nargs="*",
        help="Model definitions as name=endpoint pairs. "
             "Default: base + r9-sft with standard Modal endpoints",
    )
    parser.add_argument(
        "--episodes", type=int, default=30,
        help="Episodes per model (default: 30, paper minimum: 50 for scenario D)",
    )
    parser.add_argument(
        "--scenario", default="D", choices=list(SCENARIOS.keys()),
        help="Evaluation scenario (default: D = Open Play)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("dataset/eval"),
        help="Output directory (default: dataset/eval/)",
    )
    parser.add_argument(
        "--server-port", default="",
        help="Game server WebSocket port (default: per-model from DEFAULT_MODELS)",
    )
    parser.add_argument(
        "--username", default="",
        help="In-game username (default: per-model from DEFAULT_MODELS, no hyphens)",
    )
    parser.add_argument(
        "--project-dir", default=os.path.dirname(os.path.abspath(__file__)),
        help="Project directory",
    )
    parser.add_argument(
        "--resume", type=int, default=0,
        help="Resume from episode N (skip first N episodes)",
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="Run all models in parallel (each in its own subprocess with isolated game server)",
    )
    parser.add_argument(
        "--personality", default="",
        choices=["", "grinder", "completionist", "explorer_tinkerer"],
        help="Inject a personality block into the system prompt (default: none)",
    )
    parser.add_argument(
        "--watchdog", action="store_true",
        help="Launch a background watchdog for endpoint/process/progress health",
    )
    parser.add_argument(
        "--watchdog-interval", type=int, default=30,
        help="Watchdog poll interval seconds (default: 30)",
    )
    parser.add_argument(
        "--watchdog-stale-seconds", type=int, default=300,
        help="Watchdog stale-progress threshold seconds (default: 300)",
    )
    parser.add_argument(
        "--watchdog-kill-on-failure", action="store_true",
        help="Have watchdog terminate eval processes if it detects failure",
    )
    args = parser.parse_args()

    # Parse model definitions
    models = {}
    if args.models:
        for m in args.models:
            if "=" in m:
                name, endpoint = m.split("=", 1)
                models[name] = {"endpoint": endpoint}
            else:
                print(f"Error: model must be name=endpoint, got: {m}")
                sys.exit(1)
    else:
        models = dict(DEFAULT_MODELS)

    # Apply CLI overrides to each model config
    for name in models:
        if "username" not in models[name]:
            models[name]["username"] = args.username or f"evalbot{name.replace('-', '').title()}"
        if "server_port" not in models[name]:
            models[name]["server_port"] = args.server_port
        if args.username:
            models[name]["username"] = args.username
        if args.server_port:
            models[name]["server_port"] = args.server_port

    # Preflight checks
    print("Eval Harness — Preflight Checks")
    print(f"  Scenario: {args.scenario} — {SCENARIOS[args.scenario]['name']}")
    print(f"  Episodes: {args.episodes} per model")
    print(f"  Models:   {', '.join(models.keys())}")
    print(f"  Parallel: {args.parallel}")
    print(f"  Output:   {args.output_dir}")

    # Check MongoDB
    try:
        check = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name={MONGO_CONTAINER}"],
            capture_output=True, text=True, timeout=5,
        )
        if MONGO_CONTAINER not in check.stdout:
            print(f"\n  WARNING: MongoDB container '{MONGO_CONTAINER}' not found.")
            print(f"  DB resets will fail. Start it: docker start {MONGO_CONTAINER}")
    except FileNotFoundError:
        print("\n  WARNING: docker not found. DB resets will fail.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    watchdog_proc = None
    watchdog_log = None

    if args.watchdog:
        watchdog_proc, watchdog_log = start_eval_watchdog(
            project_dir=args.project_dir,
            output_dir=args.output_dir.resolve(),
            models=models,
            episodes=args.episodes,
            interval=args.watchdog_interval,
            stale_seconds=args.watchdog_stale_seconds,
            kill_on_failure=args.watchdog_kill_on_failure,
        )

    if args.parallel and len(models) > 1:
        # Parallel mode: launch each model as a separate subprocess of this script
        # Each gets its own game server, username, and sandbox — full isolation.
        print(f"\nLaunching {len(models)} models in parallel...")
        procs = {}
        log_files = {}
        for model_name, model_cfg in models.items():
            log_path = f"/tmp/eval_{model_name}.log"
            log_f = open(log_path, "w")
            cmd = [
                sys.executable, __file__,
                "--models", f"{model_name}={model_cfg['endpoint']}",
                "--episodes", str(args.episodes),
                "--scenario", args.scenario,
                "--output-dir", str(args.output_dir),
                "--project-dir", args.project_dir,
                "--username", model_cfg["username"],
                "--server-port", model_cfg["server_port"],
            ]
            if args.resume:
                cmd.extend(["--resume", str(args.resume)])
            if args.personality:
                cmd.extend(["--personality", args.personality])
            print(f"  {model_name}: port={model_cfg['server_port']} user={model_cfg['username']} personality={args.personality or 'none'} log={log_path}")
            procs[model_name] = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT)
            log_files[model_name] = log_f

        # Wait for all to complete, printing progress
        import time as _t
        while any(p.poll() is None for p in procs.values()):
            _t.sleep(30)
            for name, p in procs.items():
                status = "running" if p.poll() is None else f"done (rc={p.returncode})"
                # Check how many episodes completed
                results_path = args.output_dir / name / "results.json"
                ep_done = 0
                if results_path.is_file():
                    try:
                        with open(results_path) as f:
                            ep_done = len(json.load(f).get("episodes", []))
                    except Exception:
                        pass
                print(f"  [{name}] {status}, {ep_done}/{args.episodes} episodes")

        # Close log files
        for f in log_files.values():
            f.close()

        # Collect results
        all_results = {}
        for model_name in models:
            results_path = args.output_dir / model_name / "results.json"
            if results_path.is_file():
                with open(results_path) as f:
                    all_results[model_name] = json.load(f)
            else:
                all_results[model_name] = {"meta": {"ok_episodes": 0}, "metrics": {}}
            rc = procs[model_name].returncode
            if rc != 0:
                print(f"\n  WARNING: {model_name} exited with code {rc}. See /tmp/eval_{model_name}.log")
    else:
        # Sequential mode (single model or explicit sequential)
        all_results = {}
        for model_name, model_cfg in models.items():
            results = run_model_eval(
                model_name=model_name,
                endpoint=model_cfg["endpoint"],
                n_episodes=args.episodes,
                scenario=args.scenario,
                output_dir=args.output_dir,
                project_dir=args.project_dir,
                username=model_cfg.get("username", args.username or "evalbot"),
                server_port=model_cfg.get("server_port", args.server_port),
                resume_from=args.resume,
                personality=args.personality,
            )
            all_results[model_name] = results

    # Print summary
    print(f"\n{'='*60}")
    print("EVAL COMPLETE — Summary")
    print(f"{'='*60}")
    for model_name, results in all_results.items():
        meta = results.get("meta", {})
        metrics = results.get("metrics", {})
        n = meta.get("ok_episodes", 0)
        if n == 0:
            print(f"\n  {model_name}: 0 successful episodes")
            continue

        def _mean(vals):
            return sum(vals) / len(vals) if vals else 0

        print(f"\n  {model_name} ({n} episodes):")
        print(f"    Tool Parse Rate:      {_mean(metrics.get('tool_parse_rate', [])):.3f}")
        print(f"    Quest Completion Rate: {_mean(metrics.get('quest_completion_rate', [])):.3f}")
        print(f"    Quest Stages Advanced: {_mean(metrics.get('quest_stages_advanced', [])):.1f}")
        print(f"    Achievements Done:     {_mean(metrics.get('achievements_completed_delta', [])):.2f}")
        print(f"    Ach. Stages Advanced:  {_mean(metrics.get('achievement_stages_advanced', [])):.1f}")
        print(f"    Kills (mean):         {_mean(metrics.get('kills', [])):.1f}")
        print(f"    Kills DB delta:       {_mean(metrics.get('kills_db_delta', [])):.1f}")
        print(f"    XP estimated (mean):  {_mean(metrics.get('xp_estimated', [])):.0f}")
        print(f"    XP DB delta (mean):   {_mean(metrics.get('xp_db_delta', [])):.0f}")
        print(f"    XP per Turn:          {_mean(metrics.get('xp_per_turn', [])):.3f}")
        print(f"    Level reached (mean): {_mean(metrics.get('level_reached', [])):.1f}")
        print(f"    Level DB reached:     {_mean(metrics.get('level_reached_db', [])):.1f}")
        print(f"    Survival Rate:        {_mean(metrics.get('survival_rate', [])):.3f}")
        print(f"    Deaths per Session:   {_mean(metrics.get('deaths_per_session', [])):.2f}")
        print(f"    Scenario Success:     {_mean(metrics.get('success_rate', [])):.3f}")

    print(f"\nResults saved to: {args.output_dir}/")
    print("Next: python3 eval_compare.py dataset/eval/base/results.json dataset/eval/r9-sft/results.json")

    if watchdog_proc and watchdog_log:
        watchdog_log.close()


if __name__ == "__main__":
    main()
