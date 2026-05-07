# Paper Variables — Design Choices to Isolate & Defend

Living catalog of every "knob" in the Kaetram agent + training pipeline that
a reviewer can ask "why this and not X?" about. Each item is an independent
variable; toggling any one could change SFT quality or eval metrics.

Grouped by layer. Defaults come from the code at the time of writing — verify
before citing in the paper, because some are ratchets (e.g. r9 → r10).

---

## 1. Run / session / turn structure

| Variable | Current default | Where set | Alternatives | Why a reviewer cares |
|---|---|---|---|---|
| Turns per session | 150 | `play.sh:29` (`MAX_TURNS`) | 50 / 300 / unbounded | Defines episode boundary; quest completion may be capped by it. |
| Sessions per run | unbounded `while true` | `play.sh:75` | fixed N, trajectory budget | "Run" is not a well-defined unit otherwise. |
| Run duration | wall-clock hours (`--hours H`) | `scripts/restart-agent.sh` | session count, trajectory count | Wall-clock is non-comparable across harnesses with different per-turn latency. |
| Inter-session pause | 10 s | `play.sh:30` | 0 / 30+ | Affects log freshness vs throughput. |
| Reset semantics | full Mongo wipe (restart) vs preserve (resume) | `restart-agent.sh` / `resume-agent.sh` | always-reset, always-resume | Level-1 vs continued progression is a confound across sessions. |
| Per-harness session timeout | Claude unbounded; Codex `MAX_TURNS*30+300s`; OpenCode `MAX_TURNS*45s`; OpenCode rotates at 250k ctx | `cli_adapter.py:295`, `play.sh:271`, `play.sh:288` | unified timeout | "Session" doesn't mean the same thing across harnesses. |
| `quest_resume.json` injection | enabled; prepended on session start | `mcp_server/tools/observe.py`, `orchestrate.py` | disabled (true amnesia) | Sessions are not i.i.d.; carryover state is implicit memory. |

## 2. Harness layer (`cli_adapter.py`)

| Variable | Current default | Notes |
|---|---|---|
| Primary harness | Claude (`--claude`) | Only harness whose data is currently used for SFT. |
| Claude model | `sonnet` | `play.sh:12` |
| Codex model | `gpt-5.4` | `play.sh:13` |
| Gemini model | `gemini-3-flash-preview` | `play.sh:14` |
| OpenCode model | per-user `opencode.template.json` | NIM Qwen via SSE-rewriting proxy. |
| Outer system prompt | each harness adds its own (Claude Code CLI prompt, Codex `model_instructions_file` + `AGENTS.md`, Gemini `GEMINI.md`, OpenCode `AGENTS.md`) | Not stripped from training data; trained model never sees these at inference. |
| Tool-call serialization | Claude `tool_use` blocks; Codex `mcp_tool_call` items; OpenCode `part.state.output` | Normalized at extraction time — normalization itself is a choice. |
| Disallowed tools | Claude only (`CLAUDE_DISALLOWED_TOOLS`, `cli_adapter.py:21`) | Other harnesses are not equivalently fenced. |
| Reasoning capture | OpenCode requires `scripts/nim_proxy.py`; otherwise `<think>` is silently dropped | Pre-proxy OpenCode runs are non-comparable. |
| MCP timeout | 60000 ms | `cli_adapter.py:181` — same budget binds different harnesses differently. |

**Top confound:** harness ↔ model are varied simultaneously; "Claude collects best
data" cannot be separated from "Sonnet is the strongest base policy" without
same-model-different-harness or same-harness-different-model runs.

## 3. MCP tool surface

17 model-visible tools — listed in `extract_turns.py:295-317`. Choices:

- Tool count (17 vs minimal vs maximal)
- `observe()` enrichment: mob `level` + `aggressive` injected from `mob_stats.py`; resource gates surfaced from `resource_gates.py`. Reduces hallucination but bakes ground-truth into observations.
- Truncation caps: inventory ≤15, nearby_entities ≤15, quests ≤10, achievements ≤10 (`play.sh:122-126`).
- ASCII map included in every observe — spatial-reasoning prior baked in.
- `observe()` is on-demand (agent-driven) but `state_heartbeat` polls every 300 ms — two observability channels with different cadences.

## 4. Prompt layer (`prompts/`)

- `prompts/system.md` ~3.5k tokens (2,731 words), XML-tagged.
- `prompts/game_knowledge.md` pre-bakes NPC coords / quest guides → benchmarks plan-following, not exploration. Reviewers will ask for the no-knowledge baseline.
- Three personality archetypes (`grinder.md`, `completionist.md`, `explorer_tinkerer.md`) injected via `__PERSONALITY_BLOCK__`. Used as a *data factory*, not a research claim.
- `quest_resume.json` prepended on session start — long-term memory the model didn't earn.
- Personality substitution is replayed at training time for byte-parity with inference (`convert_to_qwen.py:98-102`).

## 5. Game / environment

- Game-server port stride `+10`; `apiPort = P+1` reserved (dormant).
- Per-agent Mongo db isolation by name → no cross-agent social interactions.
- Xvfb + `ffmpeg x11grab` for HLS livestream — non-trivial CPU overhead per agent.
- `yarn build` required after any Kaetram-Open JSON edit.
- Node 16/18/20 only (uWS.js).
- Agent count default = 3 — concurrent agents share machine; per-turn latency is correlated across agents.

## 6. Data extraction (`extract_turns.py` → `convert_to_qwen.py`)

- **OODA turn definition** — r10 split observe and action into separate records. Pre-r10: 0 observe calls in training (Qwen3 chat-template `<think>` drop on intermediate turns).
- **Dedup**: same-position + same-action filtered after 3 repeats — behavior-shaping, not just hygiene.
- **Navigation filtering**: wall-stuck keep first; timeout-stuck keep first only; unreachable-entity discard unless reasoning shows awareness. Each rule is opinionated.
- **Action vocabulary** (19 types) is enumerated, not learned — constrains the policy class.
- **Train/val split**: 80/20 record-level vs session-stratified — different leakage properties.
- **Loss masking**: `MASK_INPUT_TOKENS=True` (assistant-only loss) — Structured Agent Distillation (arxiv 2505.13820).
- Claude Code's outer CLI system prompt is **not** included in training data — trained model sees `prompts/system.md` only. State this explicitly in the paper.

## 7. Training (`train_modal.py` / `train_kto_modal.py`)

| Variable | Default |
|---|---|
| Base model | `unsloth/Qwen3.5-9B` (Apache 2.0) |
| LoRA rank / alpha | `r=64`, `alpha=64`, `use_rslora=False` |
| LoRA targets | q, k, v, o, gate, up, down (7 modules) |
| Learning rate | SFT 1e-4, KTO 5e-7 |
| Batch / grad_accum | 2 / 8 (eff 16) |
| Epochs | 1 |
| MAX_SEQ_LEN | 16384 |
| Warmup | 5% (SFT) / 10% (KTO) |
| Weight decay | 0.01 / 0.0 |
| KTO beta | 0.1 |
| Paraphrase augmentation | 6 system-prompt intro variants |
| Eval / save / log steps | 50 / 50 / 10 |

**rsLoRA trap (load-bearing):** rsLoRA scales `1/sqrt(r)`, not `1/r`. With
`alpha=r=64`, effective LR is 8× normal — r7 diverged because of this. Keep
`use_rslora=False`. Worth writing up as a failure case.

## 8. Eval (`eval_harness.py`, `scripts/run-eval.sh`, `tests/e2e/quests/`)

- 4 scenarios A/B/C/D with hardcoded turn budgets 100/200/150/300 — different from training's 150 → eval and training distributions don't match.
- 30 episodes per model, 2 models (base vs r9-sft) on ports 9061/9071.
- Quest tiers: `core` (5) / `bonus` (5) / `extra` / `skip` / `reachability`. The `skip` tier means some quests are excluded from scoring — needs a justification.
- Eval is fresh-Level-1; training may use resume — eval starts off-distribution from training.

## 9. Logging / observability (Heisenberg)

- `state_heartbeat` 300 ms POSTs to `/ingest/state` — read-only but JS-execution overhead.
- `ffmpeg x11grab` is real CPU.
- Activity log tail at 1 Hz.
- Dashboard is a "soft dependency" — failures are silent; no per-session heartbeat-health flag in metadata, so we can't filter out partially-degraded sessions post-hoc.

## 10. Cross-cutting / hidden

- `r9-sft` naming implies r1-r8 existed. Either report r9 only (and explain run selection) or report the full progression (and explain attrition). r7's rsLoRA divergence is the most interesting failure to write up.
- Three archetypes is a data-diversity choice, not a persona-conditioned-policy claim.
- Training data is ~100% Claude-collected; cross-harness comparisons are validation, not training. Say this loudly.
- Qwen3 chat-template `<think>` drop on intermediate turns (QwenLM/Qwen3 #1831) — affects all pre-r10 multi-turn records. Verified via `tests/unit/test_think_roundtrip.py`.

---

## Three most dangerous unisolated variables (ranked)

1. **Harness × model conflation.** Cannot separate harness quality from base
   model quality without controlled cross-conditions.
2. **`game_knowledge.md` + `quest_resume.json`.** Quest completion is measured
   on an agent that's told where NPCs are and resumes from saved state. Pick
   the framing: "learned to play Kaetram" is a much weaker claim than "learned
   to follow procedural plans given knowledge + memory."
3. **Episode-boundary definition.** 150-turn sessions with carryover state →
   sessions aren't episodes, runs aren't trials. Statistical claims need a
   clear unit-of-analysis.

---

## Action items

- [ ] Decide unit-of-analysis (session vs run vs trajectory) and back-fill it in `extract_turns.py` metadata.
- [ ] Add `harness` + `model` + `archetype` + `resume_used` flags to per-record metadata so any subset is filterable at training time.
- [ ] Add at least one no-knowledge / no-resume ablation slot to the eval matrix.
- [ ] Decide r9-only vs r1-r10 progression framing for the paper.
- [ ] Write up the rsLoRA r7 divergence as a methodological lesson.
