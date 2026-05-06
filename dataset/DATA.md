# Training Data — State & Structure

## What This Is

Raw session logs from 3 autonomous Claude agents playing Kaetram (a 2D MMORPG). Used for knowledge distillation to train a smaller Qwen model to play the game.

Each session log captures everything: the game state the agent saw, its internal reasoning (extended thinking blocks), and every action it took. This is teacher data — we're compressing Claude's gameplay knowledge into a smaller model.

---

## The 3 Personalities

Each agent has a fixed personality that shapes how it reasons and plays. This is the scientific knob for data diversity — same game, 3 orthogonal decision-making axes.

**Current archetypes (commit `2ce4792`, used for all new collection):**

| Agent | Archetype | Focus |
|-------|-----------|-------|
| agent_0 | **GRINDER** | Combat-driven leveling: target dense mob zones, sustained kill loops, low HP threshold |
| agent_1 | **COMPLETIONIST** | Quest progression: NPC-first, infrastructure quest order, conservative HP gating |
| agent_2 | **EXPLORER_TINKERER** | World + systems coverage: zone rotation, building entry, varied tool surface |

**Legacy vibe personalities (r4–r10 datasets):** AGGRESSIVE / METHODICAL / CURIOUS. The r10 SFT dataset on disk still references these names in `metadata.json` (`personality` field) because those records were collected pre-`2ce4792`. New runs write `grinder` / `completionist` / `explorer_tinkerer` instead. EFFICIENT (agent_3) was dropped earlier (45% click_tile fallback, lowest progression).

Personalities are injected via `prompts/personalities/{archetype}.md` into the system prompt at session start by `orchestrate.py` (substituted at the `__PERSONALITY_BLOCK__` placeholder).

---

## Data Layout

```
dataset/
├── raw/
│   ├── agent_0/
│   │   ├── runs/
│   │   │   ├── run_20260504_140418/  ← each restart-agent.sh creates a new run dir (EST timestamp)
│   │   │   │   ├── run.meta.json     ← run-level metadata (personality, harness, model, etc.)
│   │   │   │   ├── session_1_20260504_180418.log
│   │   │   │   ├── session_1_20260504_180418.meta.json
│   │   │   │   └── ...
│   │   │   ├── run_20260504_172157/
│   │   │   └── ...
│   │   └── logs -> runs/run_20260505_150033  ← symlink to latest run (backward compat)
│   ├── agent_1/  (same structure)
│   ├── agent_2/  (same structure)
│   └── _archive/             ← frozen out-of-corpus runs; analyze.py / extract_turns.py SKIP this tree
│       ├── claude/agent_{0,1,2}/run_*    ← pre-Core-3-refactor Claude runs (May 4 cutoff, 2026-05-06 archive)
│       ├── opencode/agent_{0,1,2}/run_*  ← experimental harness, never in training
│       ├── codex/agent_{0,1,2}/run_*
│       ├── gemini/agent_{0,1,2}/run_*
│       └── _legacy_state/                ← stray runs/state/ relics from pre-symlink layout
├── extracted/                ← OODA turns extracted from raw logs (generated, not committed)
├── qwen_sft/                 ← Final SFT training records (generated, not committed)
├── qwen_kto/                 ← KTO preference records (generated, gitignored)
└── world_model/              ← Forward dynamics model data
```

**Archive boundary (2026-05-06).** Anything older than the Core 3 refactor (May 4, 2026) — and every non-Claude harness run — was moved into `dataset/raw/_archive/<harness>/agent_N/run_*`. The active corpus under `agent_*/runs/` is the **r10 candidate set**: post-Core-3 Claude only. `parse.py:list_runs()` globs `runs/run_*` and never recurses into `_archive/`, so archived runs are invisible to `analyze.py` and the SFT extractors by default. To inspect archived data, parse the `_archive/<harness>/agent_N/run_*` paths directly.

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

**Data isolation:** Only Claude logs are used for Qwen SFT training. `extract_turns.py` skips codex/gemini format logs. `convert_to_qwen.py` filters by `INCLUDED_HARNESSES = {"claude", "unknown"}` on each turn's `harness` tag. Codex and Gemini logs exist in the same `dataset/raw/agent_N/runs/` directories but are safely excluded from the training pipeline.

---

## What's Kept and Why

**Active training data: March 28 – present (agents 0-2 only)**
The personality system was finalized on March 22 and prompts were dialed in by March 28. All training data comes from this period onward — confirmed personalities, MCP-based structured actions, clean reasoning. Only agents 0-2 (AGGRESSIVE, METHODICAL, CURIOUS) are used for training. Agent_3's legacy EFFICIENT logs and the March 19-21 backlog have been deleted from the VM.

**Deleted: March 22–27**
Personality system being built and broken mid-run. Prompt changes mid-collection, March 26 full outage day. Removed entirely.

---

## Current Dataset Stats (as of April 28, 2026)

| | Value |
|---|---|
| Active agents | 3 — GRINDER / COMPLETIONIST / EXPLORER_TINKERER capability archetypes (`prompts/personalities/*.md`, shipped 2026-04-25 via PR #29 / KAE-46). The frozen r10 dataset still references legacy AGGRESSIVE / METHODICAL / CURIOUS in its `metadata.json` `personality` field. |
| Supported harnesses | Claude (primary, training-data source); Codex, Gemini, OpenCode (experimental smoke tests, excluded from training); xAI/Grok wired through OpenCode (PR ef3bac4) |
| Total session logs on VM | ~1,422 (490 / 479 / 453 for agents 0/1/2) — re-extraction estimate ~58k records (+124% over r10's 23,382 train build) |
| SFT training records | r10 frozen at 23,382 train / 2,590 val (`dataset/qwen_sft/`, Claude-only, observe-supervision fix applied) |
| Architecture | Modular MCP package (`mcp_server/{core,tools/...}`, entry point `mcp_game_server.py` is now a 19-line stub since PR #29), 17 model-visible typed tools |
| Active SFT focus | r10 launch superseded; current focus is Sonnet → 100% Core 5 completion (`KAE-50`) before any new SFT run. Agent-side unblock pass (`live_gate_status`, `quest_resume.json`, `recent_failures` injection, `mob_stats`, `station_locations`) shipped 2026-04-27. |
| Latest completed SFT | r9 (Apr 16-17) — observe-supervision audit motivated r10 patch set. r10 dataset prepared but training not launched (pivot to Core 5 first). |

Dataset is growing. Rebuild with `scripts/collect_sft_data.sh` or manually:
```bash
python3 extract_turns.py --log-dir dataset/raw/agent_N/runs/run_YYYYMMDD_HHMMSS/ --output-dir dataset/extracted/agent_N/
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
