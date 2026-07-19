"""End-to-end truncation gate for the SFT dataset.

Verifies that every record in the built dataset, when rendered through the
exact path the trainer uses (system prompt prepended plus the metadata-selected
tool render contract), tokenizes to ≤ MAX_SEQ_LEN.

Routes through `finetune/render.py` so the test cannot drift from the gate
or the trainer. If the gate accepted a record, this test must accept it too;
if both agree on the rendered text, the trainer can't disagree at runtime.

Why we don't use apply_chat_template(tokenize=True): transformers V5 changed
that path to return a BatchEncoding dict, and `len(dict)` returns the number
of dict keys (2), not the token count. See
https://github.com/huggingface/transformers/blob/main/MIGRATION_GUIDE_V5.md
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SFT_DIR = REPO_ROOT / "dataset" / "qwen_sft"
TRAIN = SFT_DIR / "train.json"
VAL = SFT_DIR / "val.json"
METADATA = SFT_DIR / "metadata.json"

MAX_SEQ_LEN = 16384  # matches finetune/train_modal.MAX_SEQ_LEN
TOKENIZER_ID = "unsloth/Qwen3.5-9B"  # matches finetune/train_modal.MODEL_ID

# Make finetune/render.py importable without installing the package.
sys.path.insert(0, str(REPO_ROOT / "finetune"))


def _hf_tokenizer_available() -> bool:
    """True only if the tokenizer can plausibly be loaded — i.e. cached locally
    OR we have HF_TOKEN. Without one of these, `from_pretrained` makes a
    long-blocking HTTPS call that would hang the suite."""
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN"):
        return True
    cache_root = Path(os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"))
    cache_dir = cache_root / "hub" / f"models--{TOKENIZER_ID.replace('/', '--')}"
    return cache_dir.exists()


@pytest.mark.skipif(
    not (TRAIN.exists() and VAL.exists() and METADATA.exists()),
    reason="dataset not built",
)
@pytest.mark.skipif(
    not _hf_tokenizer_available(),
    reason=f"{TOKENIZER_ID} not in HF cache and no HF_TOKEN — would hang on download",
)
def test_no_record_exceeds_max_seq_len():
    """No record in train.json or val.json may tokenize to more than
    MAX_SEQ_LEN tokens via the trainer's render path. If this fires, the
    truncation gate (`convert_to_qwen._drop_overlong`) regressed or the
    dataset wasn't rebuilt after a prompt change."""
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

    records = json.loads(TRAIN.read_text()) + json.loads(VAL.read_text())
    over: list[tuple[int, int]] = []
    for i, r in enumerate(records):
        text = render_record(
            r,
            system_prompt,
            personality_suffixes,
            tok,
            rng=None,
            render_mode=contract["tool_render_mode"],
            tools=contract["tools"],
        )
        n = len(tok.encode(text, add_special_tokens=False))
        if n > MAX_SEQ_LEN:
            over.append((i, n))

    assert not over, (
        f"{len(over)} records exceed {MAX_SEQ_LEN} tokens — gate is broken "
        f"or dataset wasn't rebuilt. First offenders (index, tokens): {over[:5]}"
    )
