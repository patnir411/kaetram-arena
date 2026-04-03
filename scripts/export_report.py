#!/usr/bin/env python3
"""Export a comprehensive JSON report of all agent training data.

Parses session logs + MongoDB for a single JSON file that Claude web/mobile
can fetch and analyze via web fetch.

Output: /tmp/kaetram-export/report.json
"""

import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Add project root
PROJECT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_DIR / "dataset" / "raw"
OUTPUT = Path("/tmp/kaetram-export/report.json")

# MongoDB (optional — skip if unavailable)
try:
    import pymongo
    mongo = pymongo.MongoClient("localhost", 27017, serverSelectionTimeoutMS=2000)
    db = mongo["kaetram_devlopment"]
    db.command("ping")
    HAS_MONGO = True
except Exception:
    HAS_MONGO = False


def parse_session_log(path: Path) -> dict:
    """Extract key stats from a session JSONL log."""
    stats = {
        "file": path.name,
        "agent": path.parts[-3] if len(path.parts) >= 3 else "unknown",
        "tools": Counter(),
        "turns": 0,
        "duration_s": 0,
        "npc_interactions": [],
        "deaths": 0,
        "errors": [],
        "model": "",
        "level_start": None,
        "level_end": None,
    }

    # Extract timestamp from filename: session_N_YYYYMMDD_HHMMSS.log
    m = re.search(r'(\d{8})_(\d{6})', path.name)
    if m:
        stats["started_at"] = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]}T{m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:6]}"

    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")

                if etype == "result":
                    stats["turns"] = event.get("num_turns", 0)
                    stats["duration_s"] = event.get("duration_ms", 0) / 1000
                    stats["model"] = event.get("model", "")

                elif etype == "assistant":
                    content = event.get("message", {}).get("content", [])
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "tool_use":
                                name = c.get("name", "unknown")
                                name = name.replace("mcp__kaetram__", "")
                                stats["tools"][name] += 1

                                if name in ("interact_npc", "talk_npc"):
                                    inp = c.get("input", {})
                                    stats["npc_interactions"].append({
                                        "tool": name,
                                        "npc": inp.get("npc_name", inp.get("instance_id", "?")),
                                    })

                elif etype == "user":
                    content = event.get("message", {}).get("content", [])
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict):
                                text = str(c.get("content", "") or c.get("text", ""))
                                # Death detection: match all escaped variants
                                if re.search(r'is_dead[\\\":\s]+true', text):
                                    stats["deaths"] += 1
                                # Extract level from observe results
                                level_match = re.search(r'"level"\s*:\s*(\d+)', text)
                                if level_match:
                                    lvl = int(level_match.group(1))
                                    if lvl > 0 and lvl < 200:
                                        if stats["level_start"] is None:
                                            stats["level_start"] = lvl
                                        stats["level_end"] = lvl

    except Exception as e:
        stats["errors"].append(str(e))

    stats["tools"] = dict(stats["tools"])
    return stats


def detect_runs(sessions: list[dict]) -> list[dict]:
    """Group sessions into runs based on session number resets.

    A 'run' starts when restart-agent.sh is called (DB reset, Level 1).
    Detected by session_1 filename appearing after higher-numbered sessions.
    """
    runs = []
    current_run_sessions = []

    for s in sessions:
        # Extract session number from filename
        m = re.match(r'session_(\d+)_', s["file"])
        snum = int(m.group(1)) if m else 0

        # New run detected: session number resets to 1 (or lower than previous)
        if snum == 1 and current_run_sessions:
            runs.append(current_run_sessions)
            current_run_sessions = []

        current_run_sessions.append(s)

    if current_run_sessions:
        runs.append(current_run_sessions)

    # Build run summaries
    run_summaries = []
    for i, run_sessions in enumerate(runs):
        total_turns = sum(s["turns"] for s in run_sessions)
        total_deaths = sum(s["deaths"] for s in run_sessions)
        total_duration = sum(s.get("duration_s", 0) for s in run_sessions)

        # Level progression: first non-None start, last non-None end
        level_start = None
        level_end = None
        for s in run_sessions:
            if s["level_start"] is not None and level_start is None:
                level_start = s["level_start"]
            if s["level_end"] is not None:
                level_end = s["level_end"]

        # Tool usage across run
        run_tools = Counter()
        for s in run_sessions:
            for tool, count in s["tools"].items():
                run_tools[tool] += count

        # NPC interactions across run
        run_npcs = []
        for s in run_sessions:
            run_npcs.extend(s["npc_interactions"])

        started_at = run_sessions[0].get("started_at", "")
        ended_at = run_sessions[-1].get("started_at", "")

        run_summaries.append({
            "run_number": i + 1,
            "started_at": started_at,
            "ended_at": ended_at,
            "sessions": len(run_sessions),
            "total_turns": total_turns,
            "total_duration_s": round(total_duration),
            "total_duration_min": round(total_duration / 60, 1),
            "total_deaths": total_deaths,
            "level_start": level_start,
            "level_end": level_end,
            "level_gain": (level_end or 0) - (level_start or 0),
            "tool_usage": dict(run_tools.most_common(15)),
            "npc_interactions": len(run_npcs),
            "npcs_talked_to": list(set(n["npc"] for n in run_npcs)),
            "model": run_sessions[0].get("model", ""),
            "session_details": [
                {
                    "file": s["file"],
                    "turns": s["turns"],
                    "duration_s": round(s.get("duration_s", 0)),
                    "deaths": s["deaths"],
                    "level_start": s["level_start"],
                    "level_end": s["level_end"],
                    "top_tools": dict(Counter(s["tools"]).most_common(5)),
                }
                for s in run_sessions
            ],
        })

    return run_summaries


def get_mongo_state() -> dict:
    """Read authoritative player state from MongoDB."""
    if not HAS_MONGO:
        return {"available": False}

    usernames = ["claudebot0", "claudebot1", "claudebot2", "claudebot3"]
    agents = {}

    for uname in usernames:
        agent = {"username": uname}

        info = db.player_info.find_one({"username": uname})
        if info:
            agent["level"] = info.get("level", 1)
            agent["hp"] = info.get("hitPoints", 0)
            agent["max_hp"] = info.get("maxHitPoints", 0)
            agent["x"] = info.get("x", 0)
            agent["y"] = info.get("y", 0)

        quests_doc = db.player_quests.find_one({"username": uname})
        if quests_doc:
            quest_data = {}
            quests_list = quests_doc.get("quests", [])
            if isinstance(quests_list, list):
                for q in quests_list:
                    if isinstance(q, dict) and q.get("key"):
                        stage = q.get("stage", 0)
                        if stage > 0:
                            quest_data[q["key"]] = {"stage": stage}
            agent["quests"] = quest_data

        stats_doc = db.player_statistics.find_one({"username": uname})
        if stats_doc:
            kills = stats_doc.get("mobKills", {})
            if isinstance(kills, dict):
                agent["total_kills"] = sum(v for v in kills.values() if isinstance(v, (int, float)))

        agents[uname] = agent

    return {"available": True, "note": "Current run only — resets on restart-agent.sh", "agents": agents}


def build_report() -> dict:
    """Build the full report, grouped by agent and run."""
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "date_range": "2026-04-02 to present",
        "description": "Kaetram AI Agent training data — 4 Claude agents. Each 'run' is a fresh Level 1 start (restart-agent.sh resets DB). Runs contain multiple sessions.",
    }

    # Only include logs from April 2 onwards
    today_start = datetime(2026, 4, 2, tzinfo=timezone.utc).timestamp()

    agent_data = {}
    all_tools = Counter()
    total_turns = 0
    total_deaths = 0

    for agent_dir in sorted(RAW_DIR.glob("agent_*")):
        logs_dir = agent_dir / "logs"
        if not logs_dir.exists():
            continue
        agent_name = agent_dir.name
        log_files = sorted(logs_dir.glob("session_*.log"), key=lambda p: p.stat().st_mtime)

        sessions = []
        for lf in log_files:
            if lf.stat().st_size < 1024:
                continue
            if lf.stat().st_mtime < today_start:
                continue
            stats = parse_session_log(lf)
            sessions.append(stats)
            for tool, count in stats["tools"].items():
                all_tools[tool] += count
            total_turns += stats["turns"]
            total_deaths += stats["deaths"]

        runs = detect_runs(sessions)
        agent_data[agent_name] = {
            "total_sessions": len(sessions),
            "total_runs": len(runs),
            "runs": runs,
        }

    # Overview
    total_sessions = sum(d["total_sessions"] for d in agent_data.values())
    total_runs = sum(d["total_runs"] for d in agent_data.values())
    report["overview"] = {
        "total_sessions": total_sessions,
        "total_runs": total_runs,
        "total_turns": total_turns,
        "total_deaths": total_deaths,
        "agents": list(agent_data.keys()),
    }

    # Per-agent summary with run grouping
    report["agents"] = {}
    for agent_name, data in agent_data.items():
        # Best run = highest level_end
        best_run = max(data["runs"], key=lambda r: r.get("level_end") or 0) if data["runs"] else None
        report["agents"][agent_name] = {
            "total_sessions": data["total_sessions"],
            "total_runs": data["total_runs"],
            "best_run_level": best_run.get("level_end") if best_run else None,
            "best_run_number": best_run.get("run_number") if best_run else None,
            "runs": data["runs"],
        }

    # Global tool usage
    report["tool_usage"] = dict(all_tools.most_common(30))

    # MongoDB current state
    report["current_game_state"] = get_mongo_state()

    return report


if __name__ == "__main__":
    report = build_report()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(report, f, indent=2, default=str)
    size_kb = OUTPUT.stat().st_size / 1024
    print(f"Exported {OUTPUT} ({size_kb:.1f} KB)")
    print(f"  Runs: {report['overview']['total_runs']}")
    print(f"  Sessions: {report['overview']['total_sessions']}")
    print(f"  Turns: {report['overview']['total_turns']}")
    print(f"  Deaths: {report['overview']['total_deaths']}")
