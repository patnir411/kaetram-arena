"""Shared render path for Qwen3.5-9B SFT/serve/eval pipelines.

Single source of truth for:
  - Patching the Qwen 3.5 chat template to preserve `<think>` on intermediate
    assistant turns (QwenLM/Qwen3 #1831 — open as of May 2026; no upstream fix).
  - Building the per-record system prompt with personality substitution and
    optional intro paraphrase augmentation (training only).
  - Rendering a record to a tokenizer-ready string with the exact kwargs the
    trainer uses.

Imported by `convert_to_qwen.py` (gate), `finetune/train_modal.py`,
`finetune/serve_modal*.py`, `finetune/train_kto_modal.py`, and
`tests/unit/test_truncation.py`. Drift here is a load-bearing failure mode —
all five callers must measure or render the same thing.

Issue refs for the next maintainer:
  - QwenLM/Qwen3 #1831 : chat template silently strips <think> from assistant
                         turns before `last_query_index` in multi-turn convos.
                         https://github.com/QwenLM/Qwen3/issues/1831
  - transformers V5    : apply_chat_template(tokenize=True) returns a
                         BatchEncoding dict, not input_ids. len() of that
                         gives 2 (its dict keys), not the token count. Use
                         tokenize=False then tok.encode() separately when
                         counting tokens.
                         https://github.com/huggingface/transformers/blob/main/MIGRATION_GUIDE_V5.md
  - TRL #3927          : with assistant-only loss masking, truncation that
                         eats all assistant tokens silently zeros per-record
                         loss. The render-parity gate prevents this.
                         https://github.com/huggingface/trl/issues/3927
"""
from __future__ import annotations

import random
from typing import Optional


# ---------------------------------------------------------------------------
# System-prompt paraphrase augmentation
# ---------------------------------------------------------------------------
# Only the intro sentence is paraphrased on training rows. The body stays
# byte-identical because it contains exact type numbers, tool signatures, and
# coordinates that game-state JSON references. Validation always uses the
# original prompt.

SYSTEM_PROMPT_INTRO_VARIANTS = [
    # Variant 0 — byte-identical to prompts/system.md lines 1-9 after __USERNAME__
    # substitution. validation/gate path (rng=None) uses this implicitly because
    # build_system_prompt only paraphrases when rng is provided. CI guard:
    # tests/unit/test_prompt_parity.test_paraphrase_variants_share_body_with_system_md
    "# Kaetram Game Agent\n\nYou are KaetramAgent, an autonomous agent playing Kaetram (2D pixel MMORPG).\n\nYour goal: beat the **3-quest Kaetram benchmark** (the CORE — see `game_knowledge` → PRIMARY OBJECTIVE). These 3 are your primary objective; nothing else matters until all 3 are complete. After the Core 3 are done, other non-off-limits quests are completable for further progression. The **OFF-LIMITS** list in `game_knowledge` names quests that are broken or non-scored — don't pass `accept_quest_offer=True` for those NPCs. Grinding, exploring, and gathering exist only to serve the quest objective.\n\n`interact_npc` reads dialogue without committing. Quest acceptance is opt-in via `accept_quest_offer=True`.\n\nYou play continuously for the entire session. Do not stop, ask for help, or wait for input.",
    # Variant 1 — paraphrase preserving Core 3 framing + interact_npc opt-in.
    "# Kaetram Game Agent\n\nYou control KaetramAgent in Kaetram (2D pixel MMORPG).\n\nYour objective: complete the **3-quest Kaetram benchmark** (the CORE — defined in `game_knowledge` → PRIMARY OBJECTIVE). These three are the only thing that matters until all three are done. Once the Core 3 are finished, the **EXTRA** quests are bonus side-quests; the **Off-limits** table lists quests that are broken or non-scored, so do not pass `accept_quest_offer=True` for any NPC on that list. Combat, exploration, and gathering only matter when they advance the Core 3.\n\nUse `interact_npc` to read dialogue without commitment. Accept a quest only by passing `accept_quest_offer=True`.\n\nKeep playing non-stop for the whole session. Never pause or ask for guidance.",
    # Variant 2 — alternate phrasing, same load-bearing instructions.
    "# Kaetram Game Agent\n\nYou are KaetramAgent, an AI agent playing the Kaetram MMORPG.\n\nPrimary goal: beat the **3-quest Kaetram benchmark** — the CORE quests listed in `game_knowledge` → PRIMARY OBJECTIVE. Until all three are complete, nothing else takes priority. After the Core 3, the **EXTRA** quests are optional progression and the **Off-limits** table marks quests you must NOT accept (do not pass `accept_quest_offer=True` for those NPCs). Grind, gather, and explore only to push Core 3 progress forward.\n\n`interact_npc` reads NPC dialogue without committing to anything; quest acceptance is explicit and opt-in via `accept_quest_offer=True`.\n\nPlay autonomously for the entire session without stopping.",
    # Variant 3 — third paraphrase variant.
    "# Kaetram Game Agent\n\nAs KaetramAgent, you play Kaetram (a 2D pixel MMORPG) autonomously.\n\nYour single objective is the **3-quest Kaetram benchmark** — the CORE described in `game_knowledge` → PRIMARY OBJECTIVE. Treat the Core 3 as the only goal until they're finished; then the **EXTRA** quests are optional side-quests for further progression. The **Off-limits** table flags broken or non-scored quests; do not pass `accept_quest_offer=True` for those NPCs. Combat and exploration are tools — use them only to advance the Core 3.\n\n`interact_npc` lets you read dialogue without committing. To actually accept a quest, you must pass `accept_quest_offer=True`.\n\nContinue playing the entire session without interruption.",
]

# Body split marker — everything from this point on stays byte-identical so
# tools, entity types, and coordinates remain stable across paraphrases.
_BODY_SPLIT_MARKER = "\n\n<game_knowledge>"

# Personality placeholder location in system.md. Filled per-record from
# metadata.personality_suffixes (full personality .md content) — matches
# eval_harness.resolve_system_prompt byte-for-byte.
_PERSONALITY_PLACEHOLDER = "__PERSONALITY_BLOCK__"


# ---------------------------------------------------------------------------
# Chat template patch (QwenLM/Qwen3 #1831)
# ---------------------------------------------------------------------------

def patch_qwen_chat_template(tokenizer) -> None:
    """Patch the Qwen 3.5 chat template to preserve <think> in all turns.

    Stock template uses `loop.index0 > ns.last_query_index` to gate whether
    `reasoning_content` is rendered. This silently strips `<think>` from every
    assistant turn before the last user query in multi-turn conversations.
    See QwenLM/Qwen3 #1831 (open as of May 2026, no upstream fix).

    Raises RuntimeError if the patch target string isn't found — running
    without the patch silently corrupts CoT supervision, so failing loud
    is the only safe behavior.
    """
    template = tokenizer.chat_template
    if template is None:
        return

    old = (
        "{%- if loop.index0 > ns.last_query_index %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content + '\\n</think>\\n\\n' + content }}\n"
        "        {%- else %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n' + content }}\n"
        "        {%- endif %}"
    )
    new = (
        "{%- if reasoning_content %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content + '\\n</think>\\n\\n' + content }}\n"
        "        {%- elif loop.index0 > ns.last_query_index %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n\\n</think>\\n\\n' + content }}\n"
        "        {%- else %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n' + content }}\n"
        "        {%- endif %}"
    )

    if new in template:
        # Already patched — tokenizers saved from a patched run (e.g. a merged
        # OPD checkpoint) carry the fixed template; nothing to do.
        print("  Qwen 3.5 chat template already patched: <think> preserved in all turns")
        return

    if old not in template:
        raise RuntimeError(
            "Qwen 3.5 chat template patch target not found — tokenizer revision has changed "
            "the reasoning_content stripping block. Inspect tokenizer.chat_template, update the "
            "`old` pattern in finetune/render.py, and re-verify <think> survives in multi-turn "
            "apply_chat_template output (tests/unit/test_think_roundtrip.py). Training without "
            "the patch silently strips <think> from intermediate turns — see QwenLM/Qwen3 #1831."
        )
    tokenizer.chat_template = template.replace(old, new)
    print("  Patched Qwen 3.5 chat template: <think> now preserved in all turns")


# ---------------------------------------------------------------------------
# System-prompt build
# ---------------------------------------------------------------------------

def build_system_prompt(
    base_system_prompt: str,
    personality: Optional[str],
    personality_suffixes: dict,
    rng: Optional[random.Random] = None,
) -> str:
    """Build per-record system prompt with personality substitution.

    Substitutes `__PERSONALITY_BLOCK__` from `personality_suffixes`. If `rng`
    is provided (training path), randomly paraphrases the intro from
    SYSTEM_PROMPT_INTRO_VARIANTS — only the intro before `<game_knowledge>`,
    body stays byte-identical. `rng=None` (validation/gate path) leaves the
    intro unchanged.
    """
    personality_block = ""
    if personality and personality in personality_suffixes:
        personality_block = personality_suffixes[personality]
    sys_content = base_system_prompt.replace(_PERSONALITY_PLACEHOLDER, personality_block)

    if rng is None:
        return sys_content

    intro = rng.choice(SYSTEM_PROMPT_INTRO_VARIANTS)
    try:
        body_start = sys_content.index(_BODY_SPLIT_MARKER)
        body = sys_content[body_start:]
        sys_content = intro + body
    except ValueError:
        # Marker missing — fall back to the unparaphrased prompt.
        pass

    return sys_content


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_record(
    record: dict,
    base_system_prompt: str,
    personality_suffixes: dict,
    tokenizer,
    rng: Optional[random.Random] = None,
) -> str:
    """Render a record to a tokenizer-ready string, matching the trainer's
    render path exactly.

    System prompt is built per-record (with personality substitution and
    optional intro paraphrase) and prepended. No `tools=` kwarg — the system
    prompt already embeds the tool markdown table from `prompts/system.md`,
    and inference doesn't pass `tools=` either, so passing it here would
    create a second tool block.

    Caller MUST invoke `patch_qwen_chat_template(tokenizer)` before any
    render. This function does not check; failing to patch causes silent
    train/gate divergence on tool-call-only intermediate turns.
    """
    sys_content = build_system_prompt(
        base_system_prompt,
        record.get("personality"),
        personality_suffixes,
        rng,
    )
    messages = [{"role": "system", "content": sys_content}] + record["messages"]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=False,
    )
