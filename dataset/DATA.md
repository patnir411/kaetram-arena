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
├── extracted/                ← OODA turns extracted from active raw logs (generated, not committed)
├── qwen_sft/                 ← Final SFT training records (generated, not committed)
├── qwen_kto/                 ← KTO preference records (generated, gitignored)
├── world_model/              ← Forward dynamics model data
├── eval/                     ← Eval-harness output (model rollouts, scorecards)
└── _archive/                 ← Frozen out-of-corpus dataset builds; ignored by every active pipeline
    ├── qwen_sft_pre_core3_apr18/                      ← Apr-18 r10 build (pre-Core-3, 25,972 records)
    ├── qwen_sft_backup_*/                             ← timestamped predecessor builds
    ├── qwen_sft_pre_r10_*/                            ← pre-Core-3 / r9-era / observe-fix snapshots (archived 2026-05-06)
    ├── qwen_sft_r8_backup/
    ├── extracted_pre_core3/                           ← OODA turns extracted from pre-Core-3 logs
    ├── extracted_pre_r10_*/
    ├── qwen_kto_backup_*/
    └── legacy_agents/agent_{3,4,5}_*/                 ← deprecated agents (EFFICIENT, Qwen testbots)
```

**Archive boundary (2026-05-06).** Two `_archive/` trees enforce the post-Core-3 SFT corpus:
- `dataset/raw/_archive/<harness>/agent_N/run_*` — pre-Core-3 raw runs and every non-Claude harness run.
- `dataset/_archive/<descriptor>/` — superseded SFT builds, OODA extractions, and KTO snapshots.

`parse.py:list_runs()` globs `agent_*/runs/run_*` and `convert_to_qwen.py` walks `dataset/extracted/` — neither recurses into `_archive/`, so archived data is invisible to the live pipeline. To inspect archived builds, read the `_archive/` paths directly.

Raw logs and generated data live on the GCP VM only (`vm.example.com`). Not committed to git.

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
  "personality": "completionist",
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

## Eligibility Rule

**The active SFT corpus is Claude only.** Every non-Claude harness run and any deprecated trajectory lives under `dataset/raw/_archive/` and `dataset/_archive/` — the live build pipeline does not see them. Every record in `dataset/qwen_sft/` is on-policy with the live world contract (current quest set, current `grinder` / `completionist` / `explorer_tinkerer` archetypes).

`dataset/qwen_sft/metadata.json` stamps the per-build provenance: `version`, `built_at`, `prompt_commit`, `source_runs[]`, `harness`, `session_count`, `raw_turns`, `record_counts`, plus the `thinking_ratio` and `truncation_gate` blocks.

---

## Current Dataset Stats

| | Value |
|---|---|
| Active agents | 3 — grinder / completionist / explorer_tinkerer capability archetypes (`prompts/personalities/*.md`) |
| Supported harnesses | Claude (sole training-data source); Codex, Gemini, OpenCode (experimental smoke tests, archived under `dataset/raw/_archive/`); xAI/Grok wired through OpenCode |
| Active session logs | live count in `metadata.json::session_count` + `source_runs[]` |
| Raw OODA turns extracted | live count in `metadata.json::raw_turns` |
| SFT training records | live count in `metadata.json::record_counts` (after thinking-ratio gate ≤25% no-think + truncation gate ≤16,384 tokens) |
| Architecture | Modular MCP package (`mcp_server/{core,tools/...}`, entry point `mcp_game_server.py` is a 30-line stub), 17 model-visible typed tools |
| Active SFT focus | r10 complete (regressed 3.5× below base under the pre-R11 harness — see `research/experiments/r10-discussion.md`). r11 complete: R11 scaffold shipped May 28–Jun 4 (base-9B 12–19/30), then OPD rounds 1–3 — scaffolded 4B teacher → base-2B student, reverse-KL; Core-3 arc base 12 → r1 12 → r2 15 → **r3 18** (past the 4B teacher's 17) (`research/experiments/opd-2b.md`; data under `dataset/opd_2b/`, parked 9B lane under `dataset/opd_r11/`). |
| Latest completed SFT | r10. r9 archived. |

Rebuild with `scripts/collect_sft_data.sh` or manually:
```bash
python3 extract_turns.py --log-dir dataset/raw/agent_N/runs/run_YYYYMMDD_HHMMSS/ --output-dir dataset/extracted/agent_N/
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/
```
Mixed mode (window=3 multi-turn + ~30% single-turn observe→action) is the only mode.

Only run extraction on agents 0-2. Sessions under `_archive/` are skipped automatically.

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
