# R10 Launch Gate

> **Status: SUPERSEDED (2026-04-28).** r10 SFT was never launched. The active gate is now
> Sonnet → 100% Core 3 completion, driven by harness/prompt patches and the
> `tests/e2e/quests/` benchmark — not an SFT artifact. The frozen r10 dataset on the VM
> retains legacy AGGRESSIVE/METHODICAL/CURIOUS personality labels (per `dataset/DATA.md`)
> and is preserved as historical record. Do not re-launch on this artifact.

---

Status: `GATED` — do not launch `r10` until every item below passes.

## Lanes
- Barath: `KAE-42` patches, remaining regression tests, decode-mode alignment, smoke SFT, eval matrix
- Niral: `KAE-41`, game/source knowledge, fresh Claude collection runs

## Verified Facts (post-KAE-42, 2026-04-18)
- r9-era SFT had `0` `observe` tool_calls / `21,976` assistant turns. **Fixed: r10 dataset has `33,291` observe calls / `61,412` total (54.2%).**
- r9 training vs eval prompt diverged `11,546` vs `15,318-15,319` bytes. **Fixed: byte-exact parity via shared `prompts/personalities/*.md` path, asserted by `tests/test_prompt_parity.py`.**
- VM eval `20260417_003705_curious` N=2 confirmed r9-sft worse than base on quests/achievements — motivated this work.
- r10 dataset post-patch: `23,382` train / `2,590` val (vs r9 `5,871` / `575` — 4× data, from window_size=3 + all extracted logs).
- Post-build observe-pair dedup dropped `10 / 25,982` records (0.038%). Pre-tokenize truncation gate rejected `0 / 25,982`.
- **78 / 78 tests pass** on rebuilt dataset. 7 gate tests all green: `test_prompt_parity`, `test_observe_supervision`, `test_truncation`, `test_think_roundtrip`, `test_tool_vocab_drift`, `test_loop_noise`, `test_loss_masking`.
- Qwen3.5-9B decode params applied as thinking-general mode in `serve_modal.py` + `serve_modal_base.py`: `temp=1.0, top_p=0.95, top_k=20, presence_penalty=1.5`.

## Launch Criteria
- [x] `tests/test_prompt_parity.py` passes.
- [x] `tests/test_observe_supervision.py` passes.
- [x] `tests/test_truncation.py` exists and passes (no record > `MAX_SEQ_LEN`).
- [x] `tests/test_think_roundtrip.py` exists and passes (end-to-end tokenizer round-trip preserves `<think>` on every assistant turn, not just Jinja-fragment presence).
- [x] `tests/test_tool_vocab_drift.py` exists and passes (training tools == curated model-visible surface == live MCP export == `prompts/system.md` `<tools>` block).
- [x] `tests/test_loop_noise.py` exists and passes (no observe→observe adjacency, no 3+ identical consecutive tool names in any record).
- [x] `KAE-42` patches landed (window_size 5→3, observe→observe bigram filter + post-build dedup, observe tool_result entity caps, stale click_tile filter removed, pre-tokenize gate). See commits `603fc48`, `8f0fe98`.
- [x] Dead tools (`accept_quest`, `clear_combat`, `talk_npc`) removed from `prompts/system.md` `<tools>` block + `dataset/qwen_sft/metadata.json` tools[].
- [x] Decode mode chosen (thinking-general: `temp=1.0, top_p=0.95, top_k=20, presence_penalty=1.5`) and applied consistently in `serve_modal.py` and `serve_modal_base.py` per Qwen3.5-9B model card. See commit `007730a`.
- [x] SFT dataset rebuilt with patches applied; all six gate tests + 72 others (78 total) pass on rebuilt artifact.
- [ ] Smoke SFT (`~50` steps, H100, ~$10) shows no tool call repeated `>=5` times consecutively in any eval episode.
- [ ] Eval matrix (`none`, `aggressive`) on smoke model does not show the r9-class warp/equip/dialogue loop pathology.

## Out Of Scope
- `KAE-41` / upstream game-source fixes (Niral's lane)
- DRY sampler (not production-ready in vLLM/SGLang)
- Harness loop guardrails (P2, not blocking)
- `r=32` vs `r=64` ablation (P2; defensible experiment, not settled law)
- KTO-on-r9 decision (after SFT recipe verified)

## Execution Sequence
1. ~~Land prompt-parity and observe-supervision fixes.~~ **DONE via `160d662`.**
2. ~~Land remaining `KAE-42` patches on main.~~ **DONE via `feat/kae-42-remaining-patches`.**
3. ~~Add the four remaining regression tests.~~ **DONE — 78/78 tests pass on rebuilt dataset.**
4. ~~Align `serve_modal.py` + `serve_modal_base.py` decode params.~~ **DONE — thinking-general mode.**
5. Run smoke SFT (50 steps, ~$10 on H100).
6. Run eval matrix on `none` and `aggressive`.
7. If smoke + eval gates pass, `r10` becomes eligible to launch.

Context: see `KAE-42` for patch details and linked evidence; this doc is the launch gate, not the analysis log.
