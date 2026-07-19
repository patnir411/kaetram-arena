"""Variant-aware truncation gate.

Real bug: `convert_to_qwen._drop_overlong` measures length with `rng=None`
(variant 0 only), but the trainer renders with `rng=Random(42)` and picks
randomly from all 4 paraphrase variants per record. Variants 1-3 are
~30-120 chars longer than variant 0, so a record measured at variant-0
length 16370 can hit ~16400 under variant 1 — silently truncated by the
trainer, dropping the trailing `<|im_end|>` of the final assistant turn.

This test renders every record under EVERY variant and asserts ≤MAX_SEQ_LEN.
If it fires, either the gate must be tightened to use the max-over-variants,
or the dataset rebuilt with a tighter threshold.
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SFT_DIR = REPO_ROOT / "dataset" / "qwen_sft"
TRAIN = SFT_DIR / "train.json"
VAL = SFT_DIR / "val.json"
METADATA = SFT_DIR / "metadata.json"

MAX_SEQ_LEN = 16384
TOKENIZER_ID = "unsloth/Qwen3.5-9B"

sys.path.insert(0, str(REPO_ROOT / "finetune"))


def _tokenizer_available() -> bool:
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN"):
        return True
    cache_root = Path(
        os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    )
    cache_dir = cache_root / "hub" / f"models--{TOKENIZER_ID.replace('/', '--')}"
    return cache_dir.exists()


@pytest.mark.skipif(
    not (TRAIN.exists() and VAL.exists() and METADATA.exists()),
    reason="dataset not built",
)
@pytest.mark.skipif(
    not _tokenizer_available(),
    reason=f"{TOKENIZER_ID} not in HF cache and no HF_TOKEN — would hang on download",
)
def test_no_record_exceeds_max_seq_len_under_any_variant():
    """Render the longest records under each of the 4 paraphrase variants.

    Sampling rationale: only records near the MAX_SEQ_LEN cap can bust it
    under a longer paraphrase variant. We pick the top-N longest under
    variant 0 (which the gate measured) and re-render those under the
    other 3 variants. A short record can't grow past 16384 just because
    variants 1-3 add ~50-120 chars of intro paraphrase.

    Set N via `TRUNC_VARIANT_SAMPLE_N` env var for a deeper check. Default
    is the top 500, which catches the boundary band where the gate's
    variant-blindness actually matters.
    """
    try:
        from transformers import AutoTokenizer
    except ImportError:
        pytest.skip("transformers not installed")
    try:
        tok = AutoTokenizer.from_pretrained(TOKENIZER_ID)
    except Exception as e:
        pytest.skip(f"tokenizer fetch failed ({e.__class__.__name__}): {e}")

    from render import (  # type: ignore
        patch_qwen_chat_template,
        render_record,
        resolve_render_contract,
    )

    patch_qwen_chat_template(tok)

    metadata = json.loads(METADATA.read_text())
    system_prompt = metadata["system_prompt"]
    personality_suffixes = metadata.get("personality_suffixes", {})
    contract = resolve_render_contract(metadata)

    def render(r, rng=None):
        return render_record(
            r,
            system_prompt,
            personality_suffixes,
            tok,
            rng=rng,
            render_mode=contract["tool_render_mode"],
            tools=contract["tools"],
        )

    sample_n = int(os.environ.get("TRUNC_VARIANT_SAMPLE_N", "500"))

    train_records = json.loads(TRAIN.read_text())
    val_records = json.loads(VAL.read_text())

    over: list[tuple[str, int, int, int]] = []  # (split, idx, variant, n_tokens)

    # First pass: measure variant-0 token count (matches gate's rng=None
    # path), pick top-N longest as the sample for variants 1-3.
    train_sized: list[tuple[int, int]] = []  # (idx, n_tokens_v0)
    for i, r in enumerate(train_records):
        text = render(r, rng=None)
        n = len(tok.encode(text, add_special_tokens=False))
        train_sized.append((i, n))
        if n >= MAX_SEQ_LEN:
            over.append(("train", i, 0, n))

    # Validation: full sweep at variant 0 (validation is rendered without
    # paraphrase, so this is the full picture for val).
    for i, r in enumerate(val_records):
        text = render(r, rng=None)
        n = len(tok.encode(text, add_special_tokens=False))
        if n >= MAX_SEQ_LEN:
            over.append(("val", i, 0, n))

    # Top-N longest variant-0 train records get the variants 1-3 sweep.
    train_sized.sort(key=lambda x: -x[1])
    sample_indices = [idx for idx, _ in train_sized[:sample_n]]

    class _ForcedRng:
        """Forces a specific variant index to be selected."""

        def __init__(self, idx: int) -> None:
            self.idx = idx

        def choice(self, seq):
            return seq[self.idx]

        def random(self) -> float:
            return 0.0

        def randint(self, a: int, b: int) -> int:
            return a

    for variant_idx in range(1, 4):  # skip 0 (already measured above)
        rng = _ForcedRng(variant_idx)
        for i in sample_indices:
            r = train_records[i]
            text = render(r, rng=rng)
            n = len(tok.encode(text, add_special_tokens=False))
            if n >= MAX_SEQ_LEN:
                over.append(("train", i, variant_idx, n))

    if over:
        # Group by variant for a digestible failure message.
        by_variant: dict[int, list[tuple[str, int, int]]] = {}
        for split, idx, variant, n in over:
            by_variant.setdefault(variant, []).append((split, idx, n))
        msg_lines = [
            f"{len(over)} records render to >= {MAX_SEQ_LEN} tokens under at least one variant.",
            "Trainer truncates at max_length; records at the boundary lose their trailing "
            "<|im_end|>, breaking EOS supervision.",
            "Fix options: (a) tighten convert_to_qwen._drop_overlong to use max-over-variants "
            "and threshold n < MAX_SEQ_LEN (not <=), (b) rebuild dataset with margin.",
        ]
        for variant, items in sorted(by_variant.items()):
            msg_lines.append(
                f"  variant {variant}: {len(items)} offenders, "
                f"first 5 (split, idx, tokens): {items[:5]}"
            )
        pytest.fail("\n".join(msg_lines))
