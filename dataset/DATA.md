# Training Data — State & Structure

## What This Is

Raw session logs from 3 autonomous Claude agents playing Kaetram (a 2D MMORPG). Used for knowledge distillation to train a smaller Qwen model to play the game.

Each session log captures everything: the game state the agent saw, its internal reasoning (extended thinking blocks), and every action it took. This is teacher data — we're compressing Claude's gameplay knowledge into a smaller model.

---

## The 3 Personalities

Each agent has a fixed personality that shapes how it reasons and plays. This is the scientific knob for data diversity — same game, 3 orthogonal decision-making axes.

| Agent | Personality | Playstyle |
|-------|-------------|-----------|
| agent_0 | **AGGRESSIVE** | Rushes combat, targets hard mobs, low HP threshold (30%), pushes new zones early |
| agent_1 | **METHODICAL** | HP-gated decisions (60% threshold), needs 2+ food before quest mobs, infrastructure quest order |
| agent_2 | **CURIOUS** | NPC-first, enters every building, zone rotation every 30 turns, minimum combat between exploration |

**Why 3 not 4:** EFFICIENT (agent_3) was dropped after audit — produced 45% click_tile fallback rate and lowest level progression of any agent. The 3 remaining personalities cover orthogonal decision axes: *what to fight* (AGGRESSIVE), *when to act* (METHODICAL), *where to go* (CURIOUS).

Personalities are injected via `prompts/personalities/{personality}.md` into the system prompt at session start by `orchestrate.py`.

---

## Data Layout

```
dataset/
├── raw/
│   ├── agent_0/logs/         ← AGGRESSIVE session logs (active)
│   ├── agent_1/logs/         ← METHODICAL session logs (active)
│   └── agent_2/logs/         ← CURIOUS session logs (active)
├── extracted/                ← OODA turns extracted from raw logs (generated, not committed)
├── qwen_sft/                 ← Final SFT training records (generated, not committed)
├── qwen_kto/                 ← KTO preference records (generated, gitignored)
└── world_model/              ← Forward dynamics model data
```

Raw logs and generated data live on the GCP VM only (`34.28.111.6`). Not committed to git. Agent_3's legacy EFFICIENT logs and the pre-personality backlog were deleted after the personality system was finalized on April 3.

---

## Session Metadata

Every session log has a sidecar metadata file written alongside it:

```
session_10_20260328_081546.log         ← gameplay log
session_10_20260328_081546.meta.json   ← who wrote it
```

Example metadata:
```json
{
  "agent_id": 0,
  "personality": "aggressive",
  "harness": "claude",
  "model": "claude-sonnet-4-6",
  "username": "ClaudeBot0",
  "session": 10,
  "timestamp": "20260328_081546",
  "log_file": "session_10_20260328_081546.log"
}
```

Written automatically by `orchestrate.py` at session start. The `harness` field identifies which CLI produced the log (`"claude"`, `"codex"`, `"gemini"`). Use these to filter sessions without reading log content.

**Data isolation:** Only Claude logs are used for Qwen SFT training. `extract_turns.py` skips codex/gemini format logs. `convert_to_qwen.py` filters by `INCLUDED_HARNESSES = {"claude", "unknown"}` on each turn's `harness` tag. Codex and Gemini logs exist in the same `dataset/raw/agent_N/logs/` directories but are safely excluded from the training pipeline.

---

## What's Kept and Why

**Active training data: March 28 – present (agents 0-2 only)**
The personality system was finalized on March 22 and prompts were dialed in by March 28. All training data comes from this period onward — confirmed personalities, MCP-based structured actions, clean reasoning. Only agents 0-2 (AGGRESSIVE, METHODICAL, CURIOUS) are used for training. Agent_3's legacy EFFICIENT logs and the March 19-21 backlog have been deleted from the VM.

**Deleted: March 22–27**
Personality system being built and broken mid-run. Prompt changes mid-collection, March 26 full outage day. Removed entirely.

---

## Current Dataset Stats (as of April 14, 2026)

| | Value |
|---|---|
| Active agents (r10 dataset) | 3 (AGGRESSIVE, METHODICAL, CURIOUS — vibe personalities). New collection runs use GRINDER / COMPLETIONIST / EXPLORER capability archetypes — see `prompts/personalities/*.md`. |
| Supported harnesses | Claude (primary, training-data source); Codex, Gemini, OpenCode (experimental smoke tests, excluded from training) |
| Total session logs on VM | ~640 (220 / 213 / 207 for agents 0/1/2) |
| SFT training records | 6,419 train / 646 val (`dataset/qwen_sft/`, Claude-only, 4 filtered) |
| Architecture | Custom FastMCP package (`mcp_server/`, entry point `mcp_game_server.py`), 17 typed tools |
| Latest SFT run | r8 (COMPLETE Apr 14) — loss masking fixed via `train_on_responses_only` |

Dataset is growing. Rebuild with `scripts/collect_sft_data.sh` or manually:
```bash
python3 extract_turns.py --log-dir dataset/raw/agent_N/logs/ --output-dir dataset/extracted/agent_N/
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/ --mode mixed --format sft
```

Only run extraction on agents 0-2.

---

## Pipeline

```
raw logs (session_*.log)
    ↓  extract_turns.py
dataset/extracted/agent_N/turns.jsonl       ← (game_state, reasoning, action) triples
    ↓  convert_to_qwen.py
dataset/qwen_sft/train.json              ← conversation records for SFT
dataset/qwen_sft/val.json
    ↓  finetune/train_modal.py
Qwen3.5-9B finetuned model
```

Each training record: system prompt (game rules) + user message (game state) + assistant message (`<think>` reasoning block + structured MCP tool call).
