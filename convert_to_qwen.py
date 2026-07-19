#!/usr/bin/env python3
"""
convert_to_qwen.py — Transform extracted Claude OODA turns into Qwen3.5 9B SFT records.

Reads turns.jsonl files produced by extract_turns.py and emits training data in
the multi-turn chat format that mirrors the live MCP harness loop:

    user -> assistant(<think> + observe tool_call) -> tool(state)
         -> user -> assistant(<think> + action tool_call) -> tool(action result)

The system prompt is NOT embedded in records — train_modal injects it from
metadata.json at training time so byte-parity with eval_harness is preserved.

Mode is always mixed: window-3 multi-turn records plus a 30%-of-total sample
of single-turn observe→action pairs.

Filtering policy: EXCLUDED_AGENTS (path) + thinking-ratio cap (≤25% no-think,
per Qwen3 tech report §thinking mode fusion + Unsloth Qwen3.5 fine-tune guide)
+ pre-render truncation gate (load-bearing for TRL #3927). No content-based
filtering — the model sees every Claude teacher pattern, including double-
observes and repetitive action chains. Behavior is analyzed post-hoc.

Usage:
    python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/
"""

import argparse
import json
import random
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from tool_surface import (
    MODEL_VISIBLE_TOOL_DEFINITIONS as TOOL_DEFINITIONS,
    MODEL_VISIBLE_TOOL_SCHEMA_SHA256,
    TOOL_SCHEMA_VERSION,
)

# Render path is shared with the trainer + serve so the gate measures exactly
# what the trainer renders. See finetune/render.py for issue refs.
sys.path.insert(0, str(Path(__file__).resolve().parent / "finetune"))
from render import NATIVE_TOOLS_V1, patch_qwen_chat_template, render_record  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent

# Action types that should appear as tool calls in training records. Mirrors
# tool_surface.MODEL_VISIBLE_TOOL_NAMES exactly — extract_turns derives action_type
# from the same source. "other" turns (off-surface tool calls) are skipped.
VALID_ACTION_TYPES = {d["function"]["name"] for d in TOOL_DEFINITIONS}

# Defensive — these agent IDs have historically been used for non-Claude
# rollouts (Qwen self-play, eval). Path-segment match prevents accidental
# inclusion if a legacy run is re-extracted. Currently no such directories
# exist under dataset/raw/ or dataset/extracted/; kept as belt-and-braces.
EXCLUDED_AGENTS = {"agent_3", "agent_4", "agent_5"}

# Qwen3.5 9B context limit. TRL/Unsloth silently drop tokens past max_seq_length
# (TRL #3927: with assistant-only loss masking, this can zero per-record loss
# without warning). The truncation gate is the load-bearing safety net.
MAX_SEQ_LEN = 16384
# Tokenizer used by the gate — must match finetune/train_modal.MODEL_ID exactly
# so the gate measures the same render the trainer produces. Vocab/BPE merges
# are identical to upstream Qwen/Qwen3.5-9B; Unsloth ships a slightly different
# tool-calling template fragment, which matters when records carry tool_calls.
GATE_TOKENIZER_ID = "unsloth/Qwen3.5-9B"


# ── Provenance ──────────────────────────────────────────────────────────────

def _git_head_short() -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def _list_source_run_ids() -> list[str]:
    """Enumerate raw run IDs that fed the active corpus (post-_archive)."""
    runs: set[str] = set()
    raw_root = REPO_ROOT / "dataset" / "raw"
    if not raw_root.is_dir():
        return []
    for agent_dir in sorted(raw_root.glob("agent_*")):
        runs_dir = agent_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for rd in sorted(runs_dir.glob("run_*")):
            if rd.is_dir():
                runs.add(rd.name)
    return sorted(runs)


def _count_extracted(input_dir: Path) -> tuple[int, int]:
    """Return (session_count, total_turn_count) under input_dir."""
    sessions = 0
    turns = 0
    for jl in Path(input_dir).rglob("turns.jsonl"):
        sessions += 1
        try:
            with jl.open() as f:
                turns += sum(1 for _ in f)
        except OSError:
            pass
    return sessions, turns


# ── System prompt + personality loading (byte-parity with eval_harness) ────

def _load_system_prompt() -> str:
    """Load prompts/system.md with game_knowledge inlined.

    __PERSONALITY_BLOCK__ is left intact — train_modal substitutes it per-record
    at the same textual location eval_harness.resolve_system_prompt uses.
    """
    system_md = REPO_ROOT / "prompts" / "system.md"
    if not system_md.exists():
        raise FileNotFoundError(f"prompts/system.md not found at {system_md}")
    prompt = system_md.read_text()

    gk = REPO_ROOT / "prompts" / "game_knowledge.md"
    prompt = prompt.replace("__GAME_KNOWLEDGE_BLOCK__", gk.read_text() if gk.exists() else "")

    # eval_harness substitutions. __USERNAME__ and __SERVER_PORT__ are no-ops on
    # current system.md; kept for defense if either placeholder reappears.
    prompt = prompt.replace("__USERNAME__", "KaetramAgent")
    prompt = prompt.replace("__SERVER_PORT__", "")
    return prompt


def _load_personality_block(name: str) -> str:
    path = REPO_ROOT / "prompts" / "personalities" / f"{name}.md"
    return path.read_text() if path.exists() else ""


SYSTEM_PROMPT = _load_system_prompt()
PERSONALITY_SUFFIXES = {
    "grinder":           _load_personality_block("grinder"),
    "completionist":     _load_personality_block("completionist"),
    "explorer_tinkerer": _load_personality_block("explorer_tinkerer"),
}


# ── Tool-result helpers ─────────────────────────────────────────────────────

def _prefer_real_tool_result(raw: str | None) -> str | None:
    """Unwrap the `{"result": "<string>"}` envelope used by MCP tool_result blocks.

    Returns the inner string verbatim when the outer is exactly `{"result": <str>}`,
    otherwise returns raw unchanged. Returns None for empty/whitespace input.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        outer = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw  # not JSON — pass through
    if isinstance(outer, dict) and set(outer.keys()) == {"result"} and isinstance(outer["result"], str):
        return outer["result"]
    return raw


# ── Personality detection ──────────────────────────────────────────────────

_AGENT_PERSONALITY_MAP = {
    "agent_0": "grinder",
    "agent_1": "completionist",
    "agent_2": "explorer_tinkerer",
}


def detect_personality(session_path: Path) -> str | None:
    """Detect personality from agent_N path segment (matches restart-agent.sh)."""
    for part in session_path.parts:
        if part in _AGENT_PERSONALITY_MAP:
            return _AGENT_PERSONALITY_MAP[part]
    return None


# ── Loaders ─────────────────────────────────────────────────────────────────

def _is_excluded_agent(path: Path) -> bool:
    return any(seg in EXCLUDED_AGENTS for seg in path.parts)


def load_turns_by_session(input_dir: Path) -> dict[str, list[dict]]:
    """Load extracted turns grouped by session, preserving chronological order.

    Returned key is the session directory name (e.g. session_10_20260506_065132).
    Sessions under EXCLUDED_AGENTS are skipped. Each turn is tagged with
    `_session_path` so personality can be recovered without a second filesystem pass.
    """
    sessions: dict[str, list[dict]] = {}
    for jsonl in sorted(Path(input_dir).rglob("turns.jsonl")):
        if _is_excluded_agent(jsonl):
            continue
        turns = []
        for line in open(jsonl):
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if turns:
            for t in turns:
                t["_session_path"] = str(jsonl.parent)
            sessions[jsonl.parent.name] = turns
    return sessions


def load_session_meta(session_path: Path) -> dict:
    """Read session.meta.json (written by extract_turns) for personality +
    session number. Returns {} if missing — caller falls back to
    detect_personality and parses session# from the directory name."""
    p = session_path / "session.meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _session_n_from_dirname(name: str) -> int:
    """Parse session_<N>_<ts> → N. Falls back to 1 on malformed names."""
    parts = name.split("_")
    if len(parts) >= 2 and parts[0] == "session":
        try:
            return int(parts[1])
        except ValueError:
            pass
    return 1


# ── Message builders ────────────────────────────────────────────────────────

def _turn_call_id(turn: dict) -> str:
    """Stable per-turn tool_call id derived from turn_id."""
    tid = turn.get("turn_id", "t000")
    return f"call_{tid[-3:]}"


def build_assistant_message(turn: dict) -> dict | None:
    """Emit assistant message: optional <think>...</think> + native MCP tool_call.

    Mixed-mode SFT (Qwen3.5 Thinking Mode Fusion): turns with captured teacher
    reasoning render `<think>...</think>` + tool_call; turns where Sonnet fired
    a tool without writing CoT (common for grinding loops — attack/gather/drop)
    render with empty content + tool_call. The patched chat template
    (`finetune/render.patch_qwen_chat_template`) handles both: when content
    contains `<think>...</think>` the template auto-extracts it into
    `reasoning_content`; when content is empty, the elif branch
    (`loop.index0 > ns.last_query_index`) injects an empty
    `<think>\\n\\n</think>` per Qwen3 canonical no-think format.

    No filler placeholder. An empty source CoT is a real signal — teach the
    model that some actions don't need reasoning, don't fabricate one.
    """
    action_type = turn.get("action_type", "")
    if action_type not in VALID_ACTION_TYPES:
        return None  # off-surface tool — skip

    # Render reasoning verbatim (no length cap). _drop_overlong is the only
    # length authority — overlong records get dropped, never inner-truncated.
    # Tail-keep truncation on CoT is known to destroy reasoning structure
    # (arxiv 2512.21002, 2502.18001) since planning lives at the start.
    reasoning = (turn.get("reasoning") or "").strip()
    if reasoning:
        content = f"<think>\n{reasoning}\n</think>"
    else:
        content = ""

    tool_calls = [{
        "id": _turn_call_id(turn),
        "type": "function",
        "function": {
            "name": action_type,
            "arguments": dict(turn.get("action_input") or {}),
        },
    }]
    return {"role": "assistant", "content": content, "tool_calls": tool_calls}


def build_tool_result_message(turn: dict) -> dict | None:
    """Emit the tool message that carries the action's result back to the model.

    Returns None if there's no action_result_raw — caller should omit the tool
    message rather than fabricate one.
    """
    action_type = turn.get("action_type", "")
    if action_type not in VALID_ACTION_TYPES:
        return None
    real = _prefer_real_tool_result(turn.get("action_result_raw"))
    if real is None:
        return None
    return {
        "role": "tool",
        "content": real,
        "tool_call_id": _turn_call_id(turn),
        "name": action_type,
    }


# ── Record builders ─────────────────────────────────────────────────────────

def _build_messages(turns: list[dict], personality: str | None, session_n: int) -> list[dict] | None:
    """Convert a sequence of turns into the messages list of one record.

    First turn: user(orchestrate bootstrap) → assistant → tool. Subsequent
    turns: assistant → tool (the prior tool message is the user-side
    boundary, since Qwen's chat template renders tool messages as
    user-wrapped `<tool_response>`). Returns None if any turn cannot be
    rendered (off-surface action).

    The user message is `bootstrap.build_orchestrate_bootstrap(personality,
    session_n)` — byte-identical to what Claude actually saw at collection
    time (orchestrate.py:_build_user_prompt). State arrives via the observe
    tool_result, never via the user message.
    """
    from bootstrap import build_orchestrate_bootstrap
    bootstrap_text = build_orchestrate_bootstrap(personality, session_n)

    messages: list[dict] = []
    for turn in turns:
        asst = build_assistant_message(turn)
        if asst is None:
            return None
        if not messages or messages[-1]["role"] != "tool":
            messages.append({"role": "user", "content": bootstrap_text})
        messages.append(asst)
        tool_msg = build_tool_result_message(turn)
        if tool_msg is not None:
            messages.append(tool_msg)
    return messages


def build_multi_turn_records(
    session_turns: list[dict],
    personality: str | None,
    session_n: int,
    window_size: int = 3,
    stride: int | None = None,
) -> list[dict]:
    """Sliding-window multi-turn records: window_size consecutive turns each.

    Replay-prefix invariant: every record's first turn is an `observe`. When
    a window's natural start is an action turn, the most recent prior observe
    from the same session is prepended. This guarantees the model never sees
    a record that asks it to act without a preceding observe in context —
    without this, ~47% of multi-turn records would train Qwen to produce
    state-grounded actions from a content-free bootstrap with no observe.

    No content-based filtering — the model sees every Sonnet pattern.
    """
    if stride is None:
        stride = max(1, window_size // 2)
    n = len(session_turns)
    if n < 2:
        return []

    starts = list(range(0, n, stride))
    if starts and starts[-1] + window_size < n:
        starts.append(max(0, n - window_size))

    records = []
    for start in starts:
        window = session_turns[start : min(start + window_size, n)]
        if len(window) < 2:
            continue
        # Replay-prefix: if window starts on an action, prepend the most
        # recent prior observe so the first assistant tool_call is observe.
        if window[0].get("action_type") != "observe":
            anchor = None
            for i in range(start - 1, -1, -1):
                if session_turns[i].get("action_type") == "observe":
                    anchor = session_turns[i]
                    break
            if anchor is None:
                continue  # session has no prior observe; skip rather than
                          # ground actions on nothing
            window = [anchor] + list(window)
        msgs = _build_messages(window, personality, session_n)
        if msgs is None:
            continue
        records.append({"messages": msgs, "personality": personality})
    return records


def build_single_turn_records(
    session_turns: list[dict],
    personality: str | None,
    session_n: int,
) -> list[dict]:
    """One observe→tool_result(state)→action→tool_result(action) record per
    action turn, paired with its immediately-preceding observe.

    Action turns with no preceding observe are skipped — they have no grounded
    state to act on (matches extract_turns' invariant).
    """
    records = []
    last_observe_idx: int | None = None
    for i, turn in enumerate(session_turns):
        if turn.get("action_type") == "observe":
            last_observe_idx = i
            continue
        if last_observe_idx is None:
            continue
        if turn.get("action_type") not in VALID_ACTION_TYPES:
            continue
        pair = [session_turns[last_observe_idx], turn]
        msgs = _build_messages(pair, personality, session_n)
        if msgs is None:
            continue
        records.append({"messages": msgs, "personality": personality})
    return records


# ── Post-build gates (intentionally minimal) ────────────────────────────────

def _count_thinking(record: dict) -> tuple[int, int]:
    """Return (n_thinking_assistant, n_no_thinking_assistant) for a record."""
    n_think = 0
    n_nothink = 0
    for m in record["messages"]:
        if m.get("role") != "assistant":
            continue
        if "<think>" in (m.get("content") or ""):
            n_think += 1
        else:
            n_nothink += 1
    return n_think, n_nothink


def _enforce_thinking_ratio(
    records: list[dict],
    max_no_think_ratio: float,
    seed: int,
) -> tuple[list[dict], int]:
    """Downsample records so non-thinking assistant turns are ≤ max ratio.

    Per Qwen Team's Thinking Mode Fusion guidance for Qwen3.5 SFT: keep at
    least 75% of assistant turns reasoning-supervised; below that, reasoning
    capability degrades. Sonnet emits no-CoT tool calls ~47% of the time on
    repetitive actions (attack/gather/drop), so without this gate the corpus
    is dominated by non-thinking turns and the model unlearns CoT.

    Strategy: rank records by descending no-think share, drop them in that
    order (so pure-grind-loop records go first; mixed-mode records are
    preserved) until the global no-think share is within budget.
    """
    if not records:
        return records, 0
    annotated = [(r, *_count_thinking(r)) for r in records]
    total_think = sum(t for _, t, _ in annotated)
    total_nothink = sum(nt for _, _, nt in annotated)
    if total_think + total_nothink == 0:
        return records, 0

    current_ratio = total_nothink / (total_think + total_nothink)
    if current_ratio <= max_no_think_ratio:
        return records, 0

    # Sort by no-think share descending. Use record id as deterministic
    # tiebreaker so the seed shuffle below produces stable runs.
    rng = random.Random(seed)
    indexed = list(enumerate(annotated))
    rng.shuffle(indexed)
    indexed.sort(
        key=lambda x: (-(x[1][2] / max(1, x[1][1] + x[1][2])), x[0])
    )

    kept_think = total_think
    kept_nothink = total_nothink
    drop_set: set[int] = set()
    for idx, (_, t, nt) in indexed:
        ratio = kept_nothink / max(1, kept_think + kept_nothink)
        if ratio <= max_no_think_ratio:
            break
        drop_set.add(idx)
        kept_think -= t
        kept_nothink -= nt

    kept = [r for i, (r, _, _) in enumerate(annotated) if i not in drop_set]
    return kept, len(records) - len(kept)


def _drop_overlong(records: list[dict], tokenizer) -> tuple[list[dict], int, list[int]]:
    """Drop records whose train-time token count exceeds MAX_SEQ_LEN.

    Render path matches finetune/train_modal.load_kaetram_dataset exactly:
    same tokenizer (unsloth/Qwen3.5-9B), patched chat template, native tools=
    schema, system prompt prepended via render_record with rng=None
    (canonical intro, validation path).

    Records over MAX_SEQ_LEN are dropped wholesale; no inner truncation.
    This is the load-bearing safety net for TRL #3927:
    https://github.com/huggingface/trl/issues/3927 — with
    train_on_responses_only, a truncated record loses all assistant
    tokens to -100 masking and contributes zero gradient with no error.

    Gate uses rng=None canonical intro. Training paraphrases the intro
    per record from SYSTEM_PROMPT_INTRO_VARIANTS (~30-50 token variance);
    a borderline record at exactly MAX_SEQ_LEN with the canonical intro
    could overrun by 5-20 tokens under a different variant. Accepted
    risk; check all variants only if eval surfaces post-build truncation.

    Returns (kept_records, dropped_count, kept_token_counts_in_kept_order).
    """
    kept: list[dict] = []
    dropped = 0
    kept_counts: list[int] = []
    for r in records:
        text = render_record(
            r,
            SYSTEM_PROMPT,
            PERSONALITY_SUFFIXES,
            tokenizer,
            rng=None,
            render_mode=NATIVE_TOOLS_V1,
            tools=TOOL_DEFINITIONS,
        )
        # tokenize=False + tok.encode() separately. transformers V5 changed
        # apply_chat_template(tokenize=True) to return BatchEncoding (a dict),
        # so len() of that gives 2, not the token count.
        # https://github.com/huggingface/transformers/blob/main/MIGRATION_GUIDE_V5.md
        n = len(tokenizer.encode(text, add_special_tokens=False))
        if n > MAX_SEQ_LEN:
            dropped += 1
        else:
            kept.append(r)
            kept_counts.append(n)
    return kept, dropped, kept_counts


# ── Train/val split ─────────────────────────────────────────────────────────

def _split_train_val(
    records: list[dict],
    val_ratio: float,
    seed: int,
) -> tuple[list[dict], list[dict]]:
    """Stratified split by session, with fallback to record-level if the
    session split lands outside [val_ratio*0.5, val_ratio*2]."""
    sessions = sorted({r["_session"] for r in records})
    rng = random.Random(seed)
    rng.shuffle(sessions)
    n_val = max(1, int(len(sessions) * val_ratio))
    val_set = set(sessions[:n_val])

    train, val = [], []
    for r in records:
        s = r.pop("_session")
        (val if s in val_set else train).append(r)

    total = len(train) + len(val)
    actual = (len(val) / total) if total else 0
    if actual < val_ratio * 0.5 or actual > val_ratio * 2:
        print(f"  Session split produced ratio {actual:.2%}; falling back to record-level split")
        all_records = train + val
        rng2 = random.Random(seed)
        rng2.shuffle(all_records)
        nv = max(1, int(len(all_records) * val_ratio))
        val = all_records[:nv]
        train = all_records[nv:]
    return train, val


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Convert extracted Claude turns to Qwen3.5 SFT records."
    )
    parser.add_argument("--input", type=Path, default=Path("dataset/extracted"))
    parser.add_argument("--output", type=Path, default=Path("dataset/qwen_sft"))
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--window-size", type=int, default=3)
    parser.add_argument("--stride", type=int, default=None)
    parser.add_argument(
        "--max-no-think-ratio", type=float, default=0.25,
        help="Max share of assistant turns without <think>. See Qwen3 tech "
             "report (arxiv 2505.09388) §thinking mode fusion.",
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    sessions = load_turns_by_session(args.input)
    if not sessions:
        print("No turns found in input directory.", file=sys.stderr)
        sys.exit(1)

    def _session_meta(session_turns: list[dict], session_dirname: str) -> tuple[str | None, int]:
        """Return (personality, session_n) for a session.

        Authoritative: `session.meta.json` written by extract_turns from the
        run's `<log>.meta.json` sidecar. Falls back to legacy path-segment
        detection (agent_N → personality) and dirname parsing for runs that
        predate the sidecar.
        """
        if not session_turns:
            return None, 1
        sp = Path(session_turns[0].get("_session_path", ""))
        meta = load_session_meta(sp)
        personality = meta.get("personality") or detect_personality(sp)
        session_n = meta.get("session") or _session_n_from_dirname(session_dirname)
        return personality, session_n

    # Mixed mode: window=3 multi-turn records, plus a 30% sample of single-turn
    # observe→action pairs.
    multi_records: list[dict] = []
    single_records: list[dict] = []
    for sess, turns in sessions.items():
        personality, session_n = _session_meta(turns, sess)
        for r in build_multi_turn_records(
            turns, personality, session_n, args.window_size, args.stride,
        ):
            r["_session"] = sess
            multi_records.append(r)
    for sess, turns in sessions.items():
        personality, session_n = _session_meta(turns, sess)
        for r in build_single_turn_records(turns, personality, session_n):
            r["_session"] = sess
            single_records.append(r)

    # 30% of total ≈ 43% of multi count
    n_single = max(1, int(len(multi_records) * 0.43))
    rng = random.Random(args.seed + 1)
    sample = single_records if len(single_records) <= n_single else rng.sample(single_records, n_single)
    records = multi_records + sample
    print(f"  Mixed mode: {len(multi_records)} multi-turn + {len(sample)} single-turn")

    if not records:
        print("No records produced.", file=sys.stderr)
        sys.exit(1)

    # Thinking-ratio gate: keep ≥(1 - max_no_think_ratio) thinking turns.
    pre = len(records)
    pre_think = sum(_count_thinking(r)[0] for r in records)
    pre_nothink = sum(_count_thinking(r)[1] for r in records)
    pre_ratio = pre_nothink / max(1, pre_think + pre_nothink)
    records, n_dropped_ratio = _enforce_thinking_ratio(
        records, args.max_no_think_ratio, args.seed
    )
    post_think = sum(_count_thinking(r)[0] for r in records)
    post_nothink = sum(_count_thinking(r)[1] for r in records)
    post_ratio = post_nothink / max(1, post_think + post_nothink)
    print(
        f"  Thinking-ratio gate: pre={pre_ratio:.1%} no-think "
        f"({pre_think} think, {pre_nothink} no-think) → "
        f"post={post_ratio:.1%} ({post_think} think, {post_nothink} no-think); "
        f"dropped {n_dropped_ratio}/{pre} records"
    )

    if not records:
        print("No records survived thinking-ratio gate.", file=sys.stderr)
        sys.exit(1)

    # Truncation gate (load-bearing safety net for TRL #3927). Loads the
    # tokenizer once with the chat-template patch applied so the gate measures
    # exactly what the trainer renders.
    print(f"  Loading {GATE_TOKENIZER_ID} for truncation gate...")
    from transformers import AutoTokenizer
    gate_tokenizer = AutoTokenizer.from_pretrained(GATE_TOKENIZER_ID)
    patch_qwen_chat_template(gate_tokenizer)

    pre = len(records)
    records, n_trunc, kept_token_counts = _drop_overlong(records, gate_tokenizer)
    print(
        f"  Truncation gate: dropped {n_trunc}/{pre} records "
        f"({100*n_trunc/max(1, pre):.2f}%) over {MAX_SEQ_LEN} tokens"
    )

    if not records:
        print("No records survived truncation gate.", file=sys.stderr)
        sys.exit(1)

    train, val = _split_train_val(records, args.val_ratio, args.seed)

    sorted_kept = sorted(kept_token_counts)
    n_kept = len(sorted_kept)
    truncation_gate_meta = {
        "max_seq_len": MAX_SEQ_LEN,
        "tokenizer_id": GATE_TOKENIZER_ID,
        "patch_reference": "https://github.com/QwenLM/Qwen3/issues/1831",
        "checked": pre,
        "dropped": n_trunc,
        "kept": n_kept,
        "kept_max_tokens": sorted_kept[-1] if sorted_kept else 0,
        "kept_p99_tokens": sorted_kept[int(n_kept * 0.99)] if n_kept else 0,
        "kept_p50_tokens": sorted_kept[n_kept // 2] if n_kept else 0,
    }

    sess_count, raw_turns = _count_extracted(args.input)
    metadata = {
        "version": "native-tools-v1",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "prompt_commit": _git_head_short(),
        "harness": "claude",
        "source_runs": _list_source_run_ids(),
        "session_count": sess_count,
        "raw_turns": raw_turns,
        "record_counts": {"train": len(train), "val": len(val), "total": len(train) + len(val)},
        "thinking_ratio": {
            "max_no_think_ratio": args.max_no_think_ratio,
            "thinking_assistant_turns": post_think,
            "no_thinking_assistant_turns": post_nothink,
            "no_thinking_share": round(post_ratio, 4),
        },
        "truncation_gate": truncation_gate_meta,
        "bootstrap_source": "orchestrate",
        "personality_labels": list(PERSONALITY_SUFFIXES.keys()),
        "system_prompt": SYSTEM_PROMPT,
        "tool_render_mode": NATIVE_TOOLS_V1,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "tool_schema_sha256": MODEL_VISIBLE_TOOL_SCHEMA_SHA256,
        "tools": TOOL_DEFINITIONS,
        "personality_suffixes": PERSONALITY_SUFFIXES,
    }
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2))

    train_path = args.output / "train.json"
    val_path = args.output / "val.json"
    train_path.write_text(json.dumps(train, indent=2))
    val_path.write_text(json.dumps(val, indent=2))

    msg_counts = [len(r["messages"]) for r in train + val]
    print(f"\nConverted {len(records)} records (mixed mode, window_size={args.window_size})")
    print(f"  Messages/record: avg={sum(msg_counts)/max(1,len(msg_counts)):.1f}, max={max(msg_counts) if msg_counts else 0}")
    print(f"  Train: {len(train)} → {train_path}")
    print(f"  Val:   {len(val)} → {val_path}")

    type_counts: Counter = Counter()
    for r in train + val:
        for m in r["messages"]:
            if m["role"] == "assistant" and "tool_calls" in m:
                for tc in m["tool_calls"]:
                    type_counts[(tc.get("function") or {}).get("name", "unknown")] += 1
    print("\nTool call distribution:")
    for action, count in type_counts.most_common():
        print(f"  {action}: {count}")


if __name__ == "__main__":
    main()
