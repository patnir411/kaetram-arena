# Kaetram Arena / AgentScape — Research Knowledge Base

Compiled knowledge for the AgentScape research lab. Two independent papers: Paper 1 (Kaetram distillation, ICLR 2027) and Paper 2 (RuneScape adversarial multi-agent, planned). See `research/decisions/acquihire-trajectory.md` for company scope and competitive analysis.

**Rule:** After any training run, data rebuild, or design decision, update the relevant file here. If no file fits, create one and link it below. Without this, the wiki dies.

**Reliable maintenance flow:**
- LLM compile pass when explicitly requested: `.claude/commands/compile-research.md`
- Cheap VM-safe staleness check: `python3 scripts/check_research_staleness.py`
- VM-safe staleness check with email nudge: `python3 scripts/check_research_staleness.py --notify`
- VM cron-friendly wrapper: `scripts/run_research_staleness_check.sh`

The durable loop is VM cron + the wrapper. The wrapper first runs the staleness checker, then auto-invokes Claude Code with `/compile-research` using `claude-opus-4-6` when stale if Claude CLI is installed and authenticated on the VM. If research files changed, it stages `research/` + `session_log.md`, commits, rebases, and pushes. If Claude CLI is unavailable, it falls back to an email nudge.

---

## Experiments

- [training-runs.md](experiments/training-runs.md) — r1 through r9-SFT (+ r6-KTO smoke test): hyperparams, results, failures, what improved
- [data-quality.md](experiments/data-quality.md) — Filters applied, before/after metrics, what got cut and why

## Related Work

- [preference-learning.md](related-work/preference-learning.md) — KTO, DPO, GRPO, Tree-GRPO, Dr. GRPO, DAPO landscape + how we use them
- [agent-sft-landscape.md](related-work/agent-sft-landscape.md) — FireAct, Agent-FLAN, SAD, AgentTrek, AgentRefine, Agent-R1, ToolACE, GamingAgent — foundational agent SFT papers
- [adversarial-agent-landscape-2026-04.md](related-work/adversarial-agent-landscape-2026-04.md) — Adversarial agent safety field map: Apollo, METR, Redwood, Palisade, Haize, Far.AI + where game envs fit

## Decisions

- [why-kto-over-ppo.md](decisions/why-kto-over-ppo.md) — Binary labels from game outcomes, why KTO fits our data, computational tradeoffs
- [r7-hyperparameters.md](decisions/r7-hyperparameters.md) — Research-backed rationale for every r7 SFT + KTO parameter
- [acquihire-trajectory.md](decisions/acquihire-trajectory.md) — Workshop Labs → Thinking Machines precedent, AgentScape competitive analysis, visibility gaps, critical path to paper

## Paper

- [contribution.md](paper/contribution.md) — Paper 1: What's novel, framing, outline, key ablations needed
- [VARIABLES.md](paper/VARIABLES.md) — Design-variables catalog (KAE-49): every knob reviewers can question, grouped by layer
- [paper2-runescape-vision.md](paper/paper2-runescape-vision.md) — Paper 2: RuneScape adversarial multi-agent — research tracks, platform (LostCityRS + rs-sdk), prior work, setup TODOs

---

## Recent Major Changes (Apr 24 – May 4, 2026)

- **PR #29 — Modular MCP refactor merged.** Split monolithic `mcp_game_server.py` into typed capability modules. Reduced model-visible surface to **17 typed game tools** (was 22), keeping us below the RAG-MCP 19-tool degradation threshold. Deprecated wrappers retained for log back-compat in `extract_turns.py` only.
- **Capability archetypes shipped (KAE-46).** AGGRESSIVE/METHODICAL/CURIOUS personalities replaced by capability archetypes: **completionist / grinder / explorer_tinkerer**. Audit (n=30 hand-coded, n=731 automated) found that "task pressure dominates personality" — agents converge to similar action distributions under quest deadlines. Archetypes capture orthogonal capability axes instead of cosmetic style flavor. Closes the old "Personality ablation results" gap.
- **Tier-A unblock pass.** Cleared blocking deps for the quest benchmark — observe supervision live, prompt parity locked, eval harness wired against new archetypes.
- **OpenCode harness expanded to 6-model registry.** `--opencode-model` flag with aliases: `grok-4-1-fast`, `qwen3.5-35a3b`, `qwen3.5-397a17b`, `qwen3-80a3b`, `deepseek-v4-flash`, `deepseek-v4-pro`. Model-aware bot usernames (BigQwenBot, GrokBot, DeepSeekBot) allow per-model dashboard/log separation.
- **Economy patch (Apr 28).** Foraging gates dropped (25→10→5), mining removed from agent flow entirely, Miner shop reframed as general outfitter. Bronze/gold kits purchasable. Unblocks Herbalist's Desperation.
- **Rule 17 — death-zone exclusion.** 50-turn lockout on the mob that killed the agent, preventing death loops.
- **`analyze.py metrics`** — paper-quality 5-metric scorer for evaluating agent runs against the research metrics. Run-aggregated across every session in the run; Core 3 denominator is **10 stages** (sum of `stages` per quest from `prompts/quest_walkthroughs.json`), so partial progress moves the metric. Companion subcommands: `quest` (per-Core-3 stage timeline + reasoning at each advance + tool/error breakdown while active), `quest --cross-run` (max-stage histogram across every run per agent — answers "where do agents plateau?"), `errors --by-quest` (failures sliced by which Core 3 quest was active).
- **KAE-49 created** — design-variables catalog (`research/paper/VARIABLES.md`).
- **KAE-50 created** — quest benchmark framing for Paper 1.
- **r10 dataset rebuilt (May 6).** `dataset/qwen_sft/` regenerated from the post-Core-3 Claude corpus only: 5 runs × 3 agents = 135 sessions, 9,766 raw turns → 9,352 train / 934 val = **10,286 records**. Provenance baked into `metadata.json` (source_runs, prompt_commit, core3_only). Old r10 + 7 sibling backups + extracted/ moved to `dataset/_archive/`. The launch-gate concept retired (`docs/r10_launch_gate.md` removed) — benchmark = live Core 3 completion in `tests/e2e/quests/`, not an SFT-artifact gate.
- **DeepSeek V4 reasoning capture (Apr 29).** New SSE-rewriting proxy on `:8890` (`scripts/start-deepseek-proxy.sh`, reuses `nim_proxy.py`) brings DeepSeek V4 Pro/Flash to parity with NIM/Qwen on chain-of-thought capture. OpenCode 1.14.29 doesn't read `delta.reasoning_content` for `@ai-sdk/openai-compatible`, so without the proxy the CoT was billed but dropped. Companion: `_strip_think_tags_from_history` strips wrapped CoT from assistant message history before forwarding (DeepSeek otherwise echoes prior reasoning + emits malformed `<that>` close tags). All 6 OpenCode models now produce surfaced CoT — useful for cross-model thinking-quality analysis.
- **Tool API auto-action consolidation (Apr 29).** `attack` auto-loots on kill, `buy_item` auto-walks to NPC + opens shop, `craft_item` auto-walks to nearest crafting station. `interact_npc` return fields disambiguated into `quest_opened` / `quest_accepted` / `quest_offered` / `quest_state_changed` (was conflated). Effect on training data: shorter trajectories per quest step, fewer "navigate then act" bigrams, more interpretable quest-acceptance signal in extracted turns. Older logs in the dataset still carry the manual patterns; mixing requires harness-aware extraction.
- **Data scale milestone (May 3).** 294 runs / 1,694 sessions across 3 agents (agent_0: 102 runs/583 sessions, agent_1: 95/573, agent_2: 97/538). Rick's Roll stage-2+ prompt knowledge shipped May 1 (`154badc`).
- **Quest knowledge parity pass (May 1).** Misalignments fixed between e2e reachability tests and `game_knowledge.md` / `quest_walkthroughs.json`. Key fixes: cooking-station lookup now reads runtime `query_quest.station_locations.cooking` (was an unreachable hard-coded coord); Rick stage-2 puzzle decoys expanded; Rick + Lena turn-ins documented as **TWO `interact_npc` calls** (matches R5 test, explains "Thank you, I'm so touched" stuck-state); Mermaid level fact corrected against `mobs.json`; tomato/paprika coords drifted 1 tile, fixed; chained-craft + 2-call turn-in caveats added to GAME MECHANICS; Coder's Glitch / Glitch II / Coder's Fallacy walkthrough JSON statuses flipped to `off-limits` with `blocked_reason` populated. Unit tests refreshed against current truth; 104 passing, 2 legit skips.
- **Run-scoped log analysis + quest-stage progression (Apr 30).** `scripts/log_analysis/analyze.py` rewritten to aggregate every session in the latest run by default (was: latest session only); `--run <id>` parses every session in a past run, `--session N` drills back down. New subcommand `quest` emits per-quest stage transitions with the trigger tool, the model's reasoning at each advance, NPCs talked to, and tool/error breakdown while each quest was active; `quest --cross-run` produces a max-stage histogram across every run per agent. `errors --by-quest` slices errors by which quest was active. `metrics` denominator computed from `prompts/quest_walkthroughs.json` stage counts and uses last-vs-first-observe delta to defend against `quest_resume.json` replays. OpenCode/DeepSeek parser at parity with Claude (cost + tokens aggregated from `step_finish`, `<think>` extraction). `scripts/export_report.py` rebuilt on the same parser kernel with per-agent cross-run summaries (`agents[id].summary`); `scripts/dataset_stats.py` deleted. EST timestamps now 12-hour AM/PM. `quest_resume.json` reset leak fixed via shared `clear_sandbox_state_reset()` helper. `Tier-A` framing retired across all active docs.

---

## Gaps (articles needed but no source material yet)

- **World model evaluation** — Per-field accuracy, rollout drift, MCTS impact on gameplay. `world/evaluate.py` exists but results not compiled.
- **Agent distillation landscape (CRADLE, Voyager)** — `agent-sft-landscape.md` covers foundational papers; CRADLE and Voyager still need detailed side-by-side comparison with our MCP-based approach.
- **Self-play loop design** — STaR, ReST-EM, ETO patterns. Becomes relevant when KAE-16 starts.
- **Tool count scaling analysis** — Post PR #29: **17 typed model-visible tools** at inference (reconfirmed May 2, under the RAG-MCP 19-tool threshold; + 2 test-lane-only tools not loaded in production). Need to confirm tool selection accuracy on the trimmed surface; informs KAE-15 priority.
- **Cross-harness / cross-model comparative analysis** — Tooling complete (`analyze.py metrics` 5-metric scorer, `quest --cross-run` histograms, `errors --by-quest`). 6 OpenCode models + Claude/Codex/Gemini integrated with model-aware bot usernames. DeepSeek V4 Pro 8h run completed Apr 29 but results not formally compared. Blocking data: need at least one full multi-model parallel run with matched duration/archetype.
- **SOTA prompting compliance (system.md)** — Deferred May 1 (knowledge parity now 100%). `system.md` is ~3.5K tokens (2,731 words; within range of the 3K target from `SOTA_PROMPTING.md` but combined with `game_knowledge.md` exceeds it), 2× MUST overuse, missing `<verification>` block, rule duplication across system.md and game_knowledge.md. Tier-D prompt-architecture pass is the next prompt-layer task.

## Action Items (data pipeline)

_Completed items (r7/r8 SFT, serving, eval r8, loss masking fix, Qwen agent infra, dashboard tabs) removed — see git history for details._

- **Eval runs (r9):** r9 training COMPLETE Apr 16. Early eval showed r9-SFT underperformed base (1.5 quests / 28.5 kills / L24 vs base 2.5 / 26.5 / L20). Root cause identified → r10 P0 fixes (observe supervision, prompt parity). Full eval matrix never executed against r9.
- **r10 SFT launch:** Dataset built 2026-05-06 from the post-Core-3 corpus. LoRA training pending. Run launches `kaetram-qwen3.5-9b-r10` on Modal H100 against the rebuilt corpus; eval matrix compares r10-sft vs r9-sft vs base on the Core 3 quest benchmark.
- **Launch r9 KTO:** Deferred indefinitely — pipeline focuses on the quest-completion benchmark. Scaffolding intact (`finetune/train_kto_modal.py`, validated via r6-KTO smoke 10/10 steps).
