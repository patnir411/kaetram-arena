#!/usr/bin/env python3
"""
play_qwen.py — Long-lived Qwen agent harness with a warm-session loop.

One Python process spans many sessions. MCPClient, Chromium browser, the
in-game login, Xvfb/ffmpeg HLS pipeline, and the OpenAI client all stay alive
across context rollovers. When a session hits Qwen3.5's 16K context budget,
play_qwen rotates to a new session log file, increments
`<sandbox>/state/.session_counter`, resets `messages = [system,
bootstrap(N+1)]`, and continues — no browser cold-start, no relogin.

Process exits on:
  - context_overflow IS NOT an exit — it rolls into the next warm session
  - api_errors (4 consecutive)         → orchestrate cold-restarts
  - duration_exhausted (eval --max-duration-seconds)
  - SIGINT/SIGTERM                     → KeyboardInterrupt → clean exit

Multi-agent runs: orchestrate.py --qwen-sft / --qwen-base (one play_qwen
per agent slot, lives the whole orchestrate run barring crashes).
Eval runs: eval_harness.py spawns one play_qwen per episode with
--max-duration-seconds; sessions roll naturally within an episode.
Solo dev: invoke directly with just --endpoint + --system-prompt.

Usage (solo dev):
    python3 play_qwen.py --endpoint https://your-modal-url/v1 \
        --system-prompt prompts/system.md \
        --sandbox /tmp/kaetram_agent_4 \
        --personality completionist
"""

import argparse
import asyncio
import copy
import json
import os
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from inference_seed import derive_request_seed, validate_inference_seed
from tool_surface import (
    MODEL_VISIBLE_TOOL_DEFINITIONS,
    MODEL_VISIBLE_TOOL_NAMES,
    validate_live_tool_compatibility,
)
from scripts.isolated_python_entry import (
    isolated_contract_active,
    isolated_python_command,
)

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts" / "opd"))
from canonicalize import recover_tool_calls  # noqa: E402

# Harness-side recovery of malformed tool calls the server's parser dropped
# (KAETRAM_TOOL_RECOVERY). Off by default to preserve pure-weights eval parity;
# enabled for runs where we want the agent to act despite format defects, with
# a loud in-context format correction so the model can self-correct.
_TOOL_RECOVERY = bool(os.environ.get("KAETRAM_TOOL_RECOVERY"))
_FORMAT_NOTE = (
    "[format] Your previous call used non-canonical syntax and was auto-recovered. "
    "Emit tool calls as: <tool_call>\\n<function=NAME>\\n<parameter=key>\\nvalue\\n"
    "</parameter>\\n</function>\\n</tool_call> — never function(args) or "
    "<parameter=key=value>."
)


def resolve_mcp_python() -> str:
    """Resolve the sibling MCP server's Python runtime portably.

    A checkout-local ``.venv`` is absent in clean clones and fresh worktrees.
    Since this process already imports the required MCP/OpenAI stack, its
    active interpreter is the safe default. Operators may explicitly select a
    different executable when they intentionally maintain split runtimes.
    """
    environment = Path(sys.executable).absolute().parent.parent
    override = os.environ.get("KAETRAM_MCP_PYTHON", "").strip()
    if isolated_contract_active(environment) and override:
        raise RuntimeError(
            "KAETRAM_MCP_PYTHON is forbidden inside the isolated eval contract"
        )
    candidate = Path(override).expanduser() if override else Path(sys.executable)
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        source = "KAETRAM_MCP_PYTHON" if override else "sys.executable"
        raise RuntimeError(
            f"{source} does not identify an executable Python interpreter: "
            f"{candidate}"
        )
    # Do not resolve interpreter symlinks. Python uses the invoked path to
    # discover ``pyvenv.cfg``; resolving a venv's ``bin/python`` to the base
    # interpreter silently drops the venv and its installed MCP dependencies.
    return os.path.abspath(candidate)


def build_mcp_server_command(
    venv_python: str,
    server_script: str,
) -> list[str]:
    """Preserve the reviewed Python contract in the MCP tool subprocess."""
    environment = Path(venv_python).absolute().parent.parent
    if isolated_contract_active(environment):
        return isolated_python_command(
            venv_python,
            repo_root=Path(server_script).absolute().parent,
            environment_root=environment,
            script=Path(server_script),
        )
    return [venv_python, server_script]


# ---------------------------------------------------------------------------
# MCP client — spawns mcp_game_server.py and calls tools over stdio
# ---------------------------------------------------------------------------

class MCPClient:
    """Minimal MCP client that spawns the game server and calls tools."""

    def __init__(self, venv_python: str, server_script: str, env: dict):
        self.venv_python = venv_python
        self.server_script = server_script
        self.env = {**os.environ, **env}
        self._session = None
        self._client = None
        self._tools = {}  # name -> {description, inputSchema}

    async def connect(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command = build_mcp_server_command(
            self.venv_python, self.server_script
        )
        params = StdioServerParameters(
            command=command[0],
            args=command[1:],
            env=self.env,
        )
        self._transport = stdio_client(params)
        self._streams = await self._transport.__aenter__()
        read_stream, write_stream = self._streams
        from datetime import timedelta
        self._session = ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=120))
        await self._session.__aenter__()
        await self._session.initialize()

        # Discover tools
        result = await self._session.list_tools()
        for tool in result.tools:
            self._tools[tool.name] = {
                "description": tool.description or "",
                "inputSchema": tool.inputSchema or {"type": "object", "properties": {}},
            }
        return list(self._tools.keys())

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call an MCP tool and return the text result."""
        if not self._session:
            raise RuntimeError("MCP client not connected")
        result = await self._session.call_tool(name, arguments)
        # Concatenate text content from result
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts)

    async def close(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
        if hasattr(self, "_transport"):
            await self._transport.__aexit__(None, None, None)

    def get_tool_definitions(self, schema_source: str = "live") -> list[dict]:
        """Return live historical schemas or the versioned frozen snapshot."""
        defs = []
        for name, info in self._tools.items():
            if name not in MODEL_VISIBLE_TOOL_NAMES:
                continue
            defs.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": info["description"],
                    "parameters": info["inputSchema"],
                },
            })
        if schema_source == "live":
            return defs
        if schema_source == "canonical":
            # Runtime handshake: canonical model-visible prose is allowed to
            # differ, but execution-relevant live signatures must not.
            validate_live_tool_compatibility(defs)
            return copy.deepcopy(MODEL_VISIBLE_TOOL_DEFINITIONS)
        raise ValueError(f"unknown tool schema source: {schema_source!r}")

    def get_tool_names(self) -> list[str]:
        """Return the curated model-visible tool list."""
        return [n for n in self._tools if n in MODEL_VISIBLE_TOOL_NAMES]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Session log files (Claude stream-json shape) go to `<run_dir>/session_N_TS.log`,
# one file per warm-session rollover. SessionLogger owns rotation + the
# .session_counter file. Informational/debug prints go to stderr only —
# orchestrate redirects them to a singleton harness_stdout.log. log_analysis
# / dashboard / heartbeat all glob `session_*.log` and parse JSONL.

_THINK_RE = re.compile(r"<think>(.*?)</think>\s*(.*)", flags=re.DOTALL)


def _split_thinking(content: str) -> tuple[str | None, str | None]:
    """Return (thinking, remaining_text). Either may be None.

    Qwen3.5 emits a single inline `<think>...</think>` block at the start of
    its message; split it out so downstream parsers see the same
    one-block-per-record shape Claude produces (thinking | text | tool_use).
    """
    if not content:
        return None, None
    m = _THINK_RE.match(content)
    if m:
        thinking = (m.group(1) or "").strip() or None
        text = (m.group(2) or "").strip() or None
        return thinking, text
    return None, content.strip() or None


def _map_usage(openai_usage: dict | None) -> dict:
    """Map OpenAI's prompt_tokens/completion_tokens to Anthropic-shaped
    input_tokens/output_tokens so cost tracking + log_analysis read the same
    keys as Claude logs."""
    if not openai_usage:
        return {}
    return {
        "input_tokens": openai_usage.get("prompt_tokens", 0),
        "output_tokens": openai_usage.get("completion_tokens", 0),
    }


class SessionLogger:
    """Per-session log file + sidecar manager. Rotates on context overflow.

    State of truth for the session counter is `<sandbox>/state/.session_counter`.
    Each call to `open_next_session` reads-then-increments the counter, opens
    a new `session_<N>_<TIMESTAMP>.log` in `run_dir`, and writes a sibling
    `session_<N>_<TIMESTAMP>.meta.json` sidecar (mirrors the shape orchestrate
    used to write itself).
    """

    def __init__(self, run_dir: Path, sandbox_dir: Path, harness_meta: dict):
        self.run_dir = run_dir
        self.sandbox_dir = sandbox_dir
        self.harness_meta = harness_meta  # template merged into each sidecar
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.sandbox_dir / "state").mkdir(parents=True, exist_ok=True)
        self.session_n = self._read_counter()
        self.fh = None
        self.session_log_path: Path | None = None
        self.session_meta_path: Path | None = None

    @property
    def _counter_path(self) -> Path:
        return self.sandbox_dir / "state" / ".session_counter"

    def _read_counter(self) -> int:
        try:
            return int(self._counter_path.read_text().strip())
        except (OSError, ValueError):
            return 0

    def _write_counter(self) -> None:
        self._counter_path.write_text(str(self.session_n))

    def open_next_session(self) -> int:
        """Increment session counter, open new log + sidecar. Returns new N."""
        self.session_n += 1
        self._write_counter()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_log_path = self.run_dir / f"session_{self.session_n}_{ts}.log"
        self.session_meta_path = self.run_dir / f"session_{self.session_n}_{ts}.meta.json"
        # Line-buffered + explicit flush per emit — heartbeat tail relies on
        # complete lines being flushed before the next read.
        self.fh = open(self.session_log_path, "w", buffering=1)
        sidecar = {
            **self.harness_meta,
            "session": self.session_n,
            "timestamp": ts,
            "log_file": self.session_log_path.name,
        }
        self.session_meta_path.write_text(json.dumps(sidecar, indent=2))
        # Keep run.meta.json::session_count in sync (orchestrate set it to 0
        # at run start; we bump it as sessions roll).
        run_meta = self.run_dir / "run.meta.json"
        if run_meta.is_file():
            try:
                m = json.loads(run_meta.read_text())
                m["session_count"] = self.session_n
                run_meta.write_text(json.dumps(m, indent=2))
            except (OSError, ValueError):
                pass
        return self.session_n

    def emit(self, rec: dict) -> None:
        if self.fh:
            self.fh.write(json.dumps(rec) + "\n")
            self.fh.flush()

    def close(self) -> None:
        if self.fh:
            self.fh.close()
            self.fh = None


def log_system_init(
    logger: SessionLogger,
    personality: str,
    model: str,
    endpoint: str,
    tools: list[str],
) -> None:
    """Emit a Claude-shaped {type:'system', subtype:'init'} record at the
    top of each session log. Required by `cli_adapter.detect_log_format` to
    classify the log as 'claude'."""
    logger.emit({
        "type": "system",
        "subtype": "init",
        "session_id": f"qwen-s{logger.session_n}",
        "model": model,
        "tools": tools,
        "mcp_servers": [],
        "harness": "qwen",
        "personality": personality,
        "session_n": logger.session_n,
        "endpoint": endpoint,
        "timestamp": datetime.now().isoformat(),
    })


def log_assistant(
    logger: SessionLogger,
    turn: int,
    content: str,
    parsed_calls: list[dict] | None,
    usage: dict | None,
) -> None:
    """Emit one Claude-shaped {type:'assistant'} record per content block.

    Each record holds exactly ONE block (thinking | text | tool_use), matching
    Claude's on-disk shape — `parse_session_claude` pairs the most-recent
    thinking/text with the next tool_use.

    Token usage is stamped on the LAST assistant record in this turn so
    per-turn cost math reads it exactly once.
    """
    timestamp = datetime.now().isoformat()
    blocks: list[dict] = []
    thinking, text = _split_thinking(content or "")
    if thinking:
        blocks.append({"type": "thinking", "thinking": thinking})
    if text:
        blocks.append({"type": "text", "text": text})
    for parsed in parsed_calls or []:
        blk = {
            "type": "tool_use",
            "id": parsed.get("id", ""),
            "name": parsed.get("name", ""),
            "input": parsed.get("args", {}) or {},
        }
        if parsed.get("raw_arguments") is not None:
            blk["raw_arguments"] = parsed["raw_arguments"]
        blocks.append(blk)
    if not blocks:
        return
    mapped_usage = _map_usage(usage)
    for i, blk in enumerate(blocks):
        msg: dict = {"role": "assistant", "content": [blk]}
        if mapped_usage and i == len(blocks) - 1:
            msg["usage"] = mapped_usage
        logger.emit({
            "type": "assistant",
            "turn": turn,
            "timestamp": timestamp,
            "message": msg,
        })


def log_raw_model_emission(
    logger: SessionLogger,
    turn: int,
    content: str,
    tool_calls: object,
    usage: dict | None,
) -> None:
    """Preserve the server response before parsing or recovery rewrites.

    This record is the authoritative format-defect artifact. Downstream
    canonical assistant/tool records remain useful for replay, but cannot
    substitute for the exact content and argument strings returned by the
    model endpoint.
    """
    raw_calls = []
    for call in tool_calls or []:
        function = getattr(call, "function", None)
        raw_calls.append({
            "id": getattr(call, "id", ""),
            "name": getattr(function, "name", ""),
            "arguments": getattr(function, "arguments", None),
        })
    logger.emit({
        "type": "raw_model_emission",
        "turn": turn,
        "timestamp": datetime.now().isoformat(),
        "content": content,
        "tool_calls": raw_calls,
        "usage": _map_usage(usage),
    })


def log_tool_result(
    logger: SessionLogger, turn: int, tool_use_id: str, name: str, result: str
) -> None:
    """Emit a Claude-shaped {type:'user'} record carrying the tool_result."""
    logger.emit({
        "type": "user",
        "turn": turn,
        "timestamp": datetime.now().isoformat(),
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result,
            }],
        },
        "tool_name": name,
    })


def log_session_end(logger: SessionLogger, turn: int, reason: str) -> None:
    """Emit a Claude-shaped {type:'result'} record so `parse_session_claude`
    populates `result_summary` (num_turns, is_error, terminal_reason)."""
    logger.emit({
        "type": "result",
        "subtype": "session_end",
        "num_turns": turn,
        "result": reason,
        "terminal_reason": reason,
        "is_error": reason in ("api_errors",),
        "timestamp": datetime.now().isoformat(),
    })


def info(msg: str):
    """Informational status line → stderr (kept out of session JSONL logs)."""
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Context-budget gate
# ---------------------------------------------------------------------------
# Qwen3.5-9B was trained at max_seq_len=16,384. A "session" is bounded by
# context: when the next call would push past CONTEXT_BUDGET, the inner loop
# returns reason="context_overflow" and the outer warm-session loop rotates
# to a new session log + fresh `messages = [system, bootstrap(N+1)]`. The
# process keeps running; MCP/browser/login persist across rollovers.
#
# CONTEXT_BUDGET = 14,336 leaves room for the 2,048-token response.
MAX_SEQ_LEN = 16_384
RESPONSE_BUDGET = 2_048
CONTEXT_BUDGET = MAX_SEQ_LEN - RESPONSE_BUDGET

MODEL_ID_FOR_TOKENIZER = "unsloth/Qwen3.5-9B"
_TOKENIZER: object | None = None


def _get_tokenizer():
    """Lazy-load the Qwen tokenizer once per process (no torch needed).

    Apply the SAME chat-template patch the server uses (finetune/render.py,
    the single source of truth) so the client's token projection matches what
    SGLang renders — otherwise `<think>` handling diverges and the budget gate
    mis-counts.
    """
    global _TOKENIZER
    if _TOKENIZER is None:
        from transformers import AutoTokenizer

        from finetune.render import patch_qwen_chat_template
        _TOKENIZER = AutoTokenizer.from_pretrained(MODEL_ID_FOR_TOKENIZER)
        patch_qwen_chat_template(_TOKENIZER)
    return _TOKENIZER


def _projected_tokens(messages: list, tool_defs: list | None = None) -> int:
    """Token count of `messages` rendered with add_generation_prompt=True
    (the same shape SGLang will see). The base server renders `tools=` into
    the chat template, so the projection MUST include them or it undercounts
    by the whole tool-schema block. Passing tools is exact for base and a safe
    over-count for SFT (which the server ignores → rolls slightly early, never
    overflows the server). transformers v5 returns BatchEncoding from
    apply_chat_template(tokenize=True); pull .input_ids directly."""
    out = _get_tokenizer().apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True,
        tools=tool_defs or None,
    )
    ids = getattr(out, "input_ids", None)
    if ids is None and isinstance(out, dict):
        ids = out.get("input_ids", [])
    if ids is None:
        ids = out
    if ids and isinstance(ids[0], list):
        ids = ids[0]
    return len(ids)


# ---------------------------------------------------------------------------
# Sampling parameters — Qwen3.5 thinking-mode recommended values.
# Pinned client-side so server-side defaults never silently win.
# Source: Qwen3.5 model card (https://huggingface.co/unsloth/Qwen3.5-9B).
# ---------------------------------------------------------------------------
QWEN_THINK_TEMPERATURE = 1.0
QWEN_THINK_TOP_P = 0.95
QWEN_THINK_TOP_K = 20
QWEN_THINK_PRESENCE_PENALTY = 1.5


def _build_session_note(state_dir: Path, last_reason: str) -> str | None:
    """Programmatic, ADVISORY cross-session note derived from the last observed
    state (NOT model-authored, so it can't drift from live state). Seeds the next
    session's orientation to cut the re-derivation tax; the next session still
    `observe`s first, which confirms or invalidates this note.
    """
    try:
        gs = json.loads((state_dir / "game_state.json").read_text())
    except Exception:
        return None
    if not isinstance(gs, dict):
        return None
    parts: list[str] = []
    active = gs.get("active_quests") or []
    if isinstance(active, list) and active and isinstance(active[0], dict):
        a = active[0]
        note = f"working '{a.get('name')}' stage {a.get('stage')}"
        rem = (a.get("items_progress") or {}).get("remaining")
        if isinstance(rem, dict):
            # Only items still OWED (count>0) — listing "tomato:0" made agents
            # misread "0 remaining" as "I already have everything".
            still = ", ".join(f"{n} {k}" for k, n in rem.items()
                              if isinstance(n, (int, float)) and n > 0)
            if still:
                note += f", still need {still}"
        parts.append(note)
    # Last position + spawn-dungeon warp tag — lets the next session resume
    # mid-route (long overland trips span sessions) instead of re-deriving
    # "where am I / do I need to warp" (~57% of stage-2 sessions burned a turn
    # on this). Box matches observe._enrich_location / system.md's tutorial tile.
    pos = gs.get("pos") if isinstance(gs.get("pos"), dict) else {}
    px, py = pos.get("x"), pos.get("y")
    if isinstance(px, (int, float)) and isinstance(py, (int, float)):
        loc = f"last at ({px},{py})"
        if 300 <= px <= 360 and 860 <= py <= 920:
            loc += " — spawn dungeon, warp('mudwich') to leave (navigate can't path out)"
        parts.append(loc)
    # Rick's Roll stage-1 subgoal: fish + cook 5 shrimp on the main landmass FIRST,
    # THEN cross the door (379,388) ONCE to Rick. Carry the SUBGOAL keyed on whether
    # the shrimp exist — a prior note that pinned "go to the door" made the agent
    # walk past fishing spots while holding 0 shrimp.
    _core3 = ("Foresting", "Herbalist's Desperation", "Rick's Roll")
    finished_names = {q.get("name") for q in (gs.get("finished_quests") or [])
                      if isinstance(q, dict)}
    active_name = active[0].get("name") if (isinstance(active, list) and active
                                            and isinstance(active[0], dict)) else None
    next_core3 = next((q for q in _core3 if q not in finished_names), None)
    targeting_ricks = active_name == "Rick's Roll" or (
        active_name is None and next_core3 == "Rick's Roll")
    if targeting_ricks:
        inv = gs.get("inventory") or []
        def _held(key: str) -> int:
            return sum(int(i.get("count", 0) or 0) for i in inv
                       if isinstance(i, dict) and key in str(i.get("key", "")).lower())
        cooked = _held("cookedshrimp")
        has_roll = _held("seaweedroll") > 0
        ricks_stage = (active[0].get("stage")
                       if (active and isinstance(active[0], dict)) else None)
        past_shrimp = has_roll or (isinstance(ricks_stage, int) and ricks_stage >= 2)
        if past_shrimp:
            # Stages 2-3: Rick already took the shrimp and gave you the seaweedroll.
            # Do NOT re-fish — the only task left is delivering the roll to Lena,
            # which means crossing the stage-2 quest door. The prior stage-1 note
            # here sent stage-2 agents back to the fishing spots (run_20260717_172840).
            parts.append("Rick's Roll: you already turned in the shrimp and hold the "
                         "seaweedroll — do NOT fish or return to Rick. Navigate ONTO the "
                         "stage-2 door tile (260,229) to teleport to (425,909), then "
                         "navigate to Lena (455,924) and turn in the roll to finish")
        elif cooked >= 5:
            parts.append("Rick's Roll: you hold 5 cooked shrimp — cross the door "
                         "(379,388) ONCE to Rick (1088,833) and turn in (the seaside "
                         "is L76+, bring food)")
        else:
            parts.append("Rick's Roll: fish + cook 5 shrimp on the main landmass FIRST "
                         "(don't cross to Rick yet) — shrimp spots ~(325,360)/(336,328), "
                         "cook at (323,892); they count at turn-in")
    if gs.get("is_dead"):
        parts.append("you were dead — expect to respawn, then warp out")
    finished = [q.get("name") for q in (gs.get("finished_quests") or [])
                if isinstance(q, dict) and q.get("name")]
    if finished:
        parts.append(f"finished: {', '.join(finished)}")
    if last_reason and last_reason not in ("context_overflow", "interrupted"):
        parts.append(f"last session ended on {last_reason}")
    if not parts:
        return None
    return ("Note from your prior session (ADVISORY — `observe` first to confirm; "
            "ignore if it conflicts with what you see): " + "; ".join(parts) + ".")


async def _run_inner_loop(
    *,
    client: OpenAI,
    mcp: "MCPClient",
    tool_defs: list[dict],
    messages: list[dict],
    state_dir: Path,
    logger: SessionLogger,
    args,
    deadline: float | None,
) -> tuple[str, int]:
    """Run one session's inner OODA loop until it exits.

    Returns (reason, turns_played) where reason is one of:
      - "context_overflow"  — projected tokens exceed CONTEXT_BUDGET
      - "api_errors"        — 4 consecutive API failures
      - "duration_exhausted" — wall-clock deadline reached (eval only)
    KeyboardInterrupt propagates to the caller.
    """
    turn = 0
    consecutive_errors = 0

    while True:
        # Wall-clock check (eval only — orchestrate doesn't pass --max-duration-seconds).
        if deadline is not None and time.monotonic() >= deadline:
            return "duration_exhausted", turn

        # Pre-call overflow gate: end the session cleanly when next call
        # would push past Qwen's trained context window.
        projected = _projected_tokens(messages, tool_defs)
        if projected > CONTEXT_BUDGET:
            info(f"  [{turn}] Context budget reached ({projected} > {CONTEXT_BUDGET}); rolling session.")
            return "context_overflow", turn

        turn += 1

        # Sampling params pinned to Qwen3.5 thinking-mode recommendations.
        # top_k + presence_penalty go in extra_body (not OpenAI-spec; SGLang
        # forwards them). `tools=` is sent uniformly — server decides whether
        # to render them in the chat template (base) or ignore (SFT).
        try:
            completion_kwargs = dict(
                model=args.model,
                messages=messages,
                tools=tool_defs,
                temperature=QWEN_THINK_TEMPERATURE,
                top_p=QWEN_THINK_TOP_P,
                max_tokens=RESPONSE_BUDGET,
                extra_body={
                    "top_k": QWEN_THINK_TOP_K,
                    "presence_penalty": QWEN_THINK_PRESENCE_PENALTY,
                },
            )
            inference_seed = getattr(args, "inference_seed", None)
            if inference_seed is not None:
                completion_kwargs["seed"] = derive_request_seed(
                    inference_seed, logger.session_n, turn
                )
            response = client.chat.completions.create(**completion_kwargs)
            choice = response.choices[0]
            usage = response.usage.model_dump() if getattr(response, "usage", None) else None
            consecutive_errors = 0
        except Exception as e:
            info(f"  [{turn}] API error: {e}")
            consecutive_errors += 1
            if consecutive_errors > 3:
                info("Too many API errors, ending session.")
                return "api_errors", turn
            time.sleep(5)
            continue

        content = choice.message.content or ""
        tool_calls = choice.message.tool_calls
        log_raw_model_emission(logger, turn, content, tool_calls, usage)

        if content:
            display = re.sub(r"<think>.*?</think>", "[think]", content, flags=re.DOTALL)
            info(f"  [{turn}] Assistant: {display[:120]}...")

        # Route 1: structured tool_calls (server parsed XML into tool_calls).
        if tool_calls:
            structured_calls = []
            parsed_calls = []
            for tc in tool_calls:
                fn_name = tc.function.name
                raw_unparsed = None
                try:
                    fn_args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                except json.JSONDecodeError:
                    # The server salvaged a function name from malformed tool-call
                    # XML but its arguments didn't parse. Keep the raw string for
                    # the session log — it's the only evidence of what the model
                    # actually emitted (r1 eval: 8.3% of arg-requiring calls).
                    fn_args = {}
                    raw_unparsed = tc.function.arguments
                    info(f"  [{turn}] WARN malformed tool args for {fn_name}: "
                         f"{str(raw_unparsed)[:200]!r}")
                structured_calls.append({
                    "id": tc.id,
                    "type": "function",
                    # arguments MUST be a dict (matches convert_to_qwen.py:279
                    # training-time render). Qwen3.5 chat_template line 120
                    # does `tool_call.arguments | items` which requires a
                    # mapping; passing a JSON string here crashes apply_chat_template
                    # with "Can only get item pairs from a mapping" on turn 2+.
                    "function": {"name": fn_name, "arguments": fn_args},
                })
                parsed = {"name": fn_name, "args": fn_args, "id": tc.id}
                if raw_unparsed is not None:
                    parsed["raw_arguments"] = raw_unparsed
                parsed_calls.append(parsed)

            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": structured_calls,
            })
            log_assistant(logger, turn, content, parsed_calls, usage=usage)

            for parsed in parsed_calls:
                fn_name = parsed["name"]
                fn_args = parsed["args"]
                info(f"  [{turn}] → {fn_name}({fn_args})")
                try:
                    result = await mcp.call_tool(fn_name, fn_args)
                except Exception as e:
                    result = f"Error: {e}"
                info(f"  [{turn}] ← {result[:120]}...")

                # Native role=tool — matches build_tool_result_message in
                # convert_to_qwen.py. SGLang renders as <tool_response>.
                messages.append({
                    "role": "tool",
                    "content": result,
                    "tool_call_id": parsed["id"],
                    "name": fn_name,
                })
                log_tool_result(logger, turn, parsed["id"], fn_name, result)

                # Save game state for dashboard when model calls observe.
                if fn_name == "observe" and "\n\nASCII_MAP:" in result:
                    try:
                        (state_dir / "game_state.json").write_text(result.split("\n\nASCII_MAP:")[0])
                    except Exception:
                        pass

        elif content and _TOOL_RECOVERY and recover_tool_calls(content):
            # The server's parser dropped a malformed tool call (e.g.
            # `<function=gather("Oak")>`), leaving it in content. Recover the
            # executable call, rewrite history to a CLEAN canonical assistant
            # turn (severs the in-context copy prior — the model no longer sees
            # its own malformed exemplar), execute, and return a loud format
            # note so it self-corrects. Env-gated (KAETRAM_TOOL_RECOVERY).
            recovered = recover_tool_calls(content)
            reasoning = re.split(r"<tool_call>|<function=", content, maxsplit=1)[0].rstrip()
            structured_calls, parsed_calls = [], []
            for i, rc in enumerate(recovered):
                cid = f"recovered_{turn}_{i}"
                structured_calls.append({
                    "id": cid, "type": "function",
                    "function": {"name": rc["name"], "arguments": rc["args"]},
                })
                parsed_calls.append({"name": rc["name"], "args": rc["args"], "id": cid})
            messages.append({
                "role": "assistant", "content": reasoning,
                "tool_calls": structured_calls,
            })
            log_assistant(logger, turn, reasoning, parsed_calls, usage=usage)
            info(f"  [{turn}] RECOVERED {len(recovered)} malformed call(s): "
                 f"{[(p['name'], p['args']) for p in parsed_calls]}")
            for parsed in parsed_calls:
                try:
                    result = await mcp.call_tool(parsed["name"], parsed["args"])
                except Exception as e:
                    result = f"Error: {e}"
                result = f"{_FORMAT_NOTE}\n\n{result}"
                messages.append({
                    "role": "tool", "content": result,
                    "tool_call_id": parsed["id"], "name": parsed["name"],
                })
                log_tool_result(logger, turn, parsed["id"], parsed["name"], result)
                if parsed["name"] == "observe" and "\n\nASCII_MAP:" in result:
                    try:
                        (state_dir / "game_state.json").write_text(result.split("\n\nASCII_MAP:")[0])
                    except Exception:
                        pass

        elif content:
            # Text-only response (no recoverable tool call). Log and continue —
            # model is trained to emit structured tool_calls; divergence is a
            # serving-side bug, not papered over here.
            messages.append({"role": "assistant", "content": content})
            log_assistant(logger, turn, content, None, usage=usage)
            if choice.finish_reason == "stop":
                info(f"  [{turn}] Model stopped (no tool call). Continuing...")
                time.sleep(2)


async def run_agent(args):
    sandbox = Path(args.sandbox)
    state_dir = sandbox / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    tool_schema_source = getattr(
        args,
        "tool_schema_source",
        os.environ.get("KAETRAM_TOOL_SCHEMA_SOURCE", "live"),
    )
    inference_seed = getattr(args, "inference_seed", None)
    if inference_seed is not None:
        inference_seed = validate_inference_seed(inference_seed)

    # Resolve run_dir: explicit flag wins, else fall back to <sandbox>/logs
    # (back-compat for solo dev invocations).
    run_dir = Path(args.run_dir) if args.run_dir else (sandbox / "logs")

    # Harness sidecar template — orchestrate / eval_harness write this; solo
    # dev invocations fall through to defaults.
    harness_meta: dict = {
        "agent_id": int(os.environ.get("AGENT_ID", "0")),
        "personality": args.personality,
        "harness": "qwen",
        "model": args.model,
        "username": os.environ.get("KAETRAM_USERNAME", "QwenCompletionist"),
        "auth_mode": "subscription",
        "max_budget_usd": None,
        "tool_schema_source": tool_schema_source,
        "inference_seed": inference_seed,
    }
    if args.harness_meta and os.path.isfile(args.harness_meta):
        try:
            harness_meta.update(json.loads(open(args.harness_meta).read()))
        except Exception as e:
            info(f"WARN: failed to load --harness-meta {args.harness_meta}: {e}")
    # The request-driving CLI value is authoritative; a stale sidecar template
    # must not misattribute sampling provenance.
    harness_meta["inference_seed"] = inference_seed
    # Recovery is controlled only by this process environment. Persist the
    # effective Boolean after loading any sidecar so stale metadata cannot
    # misattribute an off/on factorial arm.
    harness_meta["tool_recovery_enabled"] = _TOOL_RECOVERY

    logger = SessionLogger(run_dir, sandbox, harness_meta)
    mcp = None

    # Resolve endpoint indirection only inside this process. Factorial evals use
    # --endpoint-env so signed URLs never appear in argv or persisted logs.
    endpoint_env = getattr(args, "endpoint_env", "")
    if endpoint_env:
        endpoint = os.environ.get(endpoint_env, "")
        if not endpoint:
            raise RuntimeError(f"endpoint environment variable is empty: {endpoint_env}")
        endpoint_ref = f"env:{endpoint_env}"
    else:
        endpoint = args.endpoint
        endpoint_ref = "direct-endpoint"
    args.endpoint = endpoint

    # Init OpenAI client (Modal SGLang endpoint) — shared across warm sessions.
    client = OpenAI(base_url=endpoint, api_key=args.api_key or "not-needed", timeout=300)

    # Spawn MCP game server — shared across warm sessions. Browser stays
    # logged in for the entire process lifetime; sessions only reset the
    # conversation.
    project_dir = args.project_dir
    venv_python = resolve_mcp_python()
    server_script = os.path.join(project_dir, "mcp_game_server.py")

    # Register signal handlers so cleanup runs on SIGTERM/SIGINT
    def _signal_handler(sig, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    mcp_env = {
        "KAETRAM_PORT": args.server_port or "",
        "KAETRAM_USERNAME": os.environ.get("KAETRAM_USERNAME", "QwenCompletionist"),
        "KAETRAM_EXTRACTOR": os.path.join(project_dir, "state_extractor.js"),
        "KAETRAM_STATE_DIR": str(state_dir),
    }
    # Forward observe-compaction opt-in (drops the ASCII map from observe to buy
    # turns/session) to the MCP subprocess when the launcher enabled it.
    if os.environ.get("KAETRAM_OBSERVE_COMPACT"):
        mcp_env["KAETRAM_OBSERVE_COMPACT"] = "1"
    # Held-out eval policy: redact walkthrough/advisory fields for exactly the
    # preregistered quest at the query_quest tool boundary. These variables are
    # set only by eval_harness; normal collection/inference is unchanged.
    if os.environ.get("KAETRAM_NO_WALKTHROUGH"):
        mcp_env["KAETRAM_NO_WALKTHROUGH"] = os.environ["KAETRAM_NO_WALKTHROUGH"]
    if os.environ.get("KAETRAM_HELDOUT_QUEST"):
        mcp_env["KAETRAM_HELDOUT_QUEST"] = os.environ["KAETRAM_HELDOUT_QUEST"]

    mcp = MCPClient(venv_python, server_script, mcp_env)
    info("Connecting to MCP game server...")
    tool_names = await mcp.connect()
    info(f"MCP connected. Tools: {tool_names}")

    # OpenAI-format tool definitions. Sent on every chat completion so the
    # serve endpoint can pass them to apply_chat_template if needed:
    #   - serve_modal_base.py honors them — Qwen3.5's native chat template
    #     renders the tool spec + the `<tool_call><function=...>...</function>
    #     </tool_call>` format reminder.
    #   - historical r10 serve_modal.py ignores them.
    #   - native_tools_v1 checkpoints require the canonical frozen schema.
    # The default remains live so published OPD/r10 evaluations do not change.
    tool_defs = mcp.get_tool_definitions(tool_schema_source)

    # Load system prompt once.
    system_prompt = ""
    if args.system_prompt and os.path.isfile(args.system_prompt):
        system_prompt = open(args.system_prompt).read()
    elif args.system_prompt:
        system_prompt = args.system_prompt

    from bootstrap import build_orchestrate_bootstrap
    bootstrap_personality = None if args.personality == "none" else args.personality

    # Wall-clock deadline (eval only). orchestrate doesn't pass this.
    process_start = time.monotonic()
    deadline = process_start + args.max_duration_seconds if args.max_duration_seconds else None

    info(
        f"Warm-loop started: personality={args.personality}, "
        f"endpoint={endpoint_ref}, "
        f"tool_schema_source={tool_schema_source}, "
        f"max_duration_seconds={args.max_duration_seconds or 'unbounded'}"
    )

    last_session_turn = 0
    last_reason = "interrupted"
    session_note: str | None = None
    try:
        while True:
            # Wall-clock budget check at session boundary.
            if deadline is not None and time.monotonic() >= deadline:
                info("Duration budget exhausted; exiting warm loop.")
                break

            # Open next session (rotates log file, increments counter, writes sidecar).
            session_n = logger.open_next_session()
            log_system_init(
                logger,
                personality=args.personality,
                model=args.model,
                endpoint=endpoint_ref,
                tools=tool_names,
            )

            # Fresh conversation: same system prompt, fresh bootstrap with
            # the new session_n so the model sees "Session #N".
            bootstrap_text = build_orchestrate_bootstrap(bootstrap_personality, session_n)
            if session_note:
                bootstrap_text += "\n\n" + session_note
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": bootstrap_text},
            ]

            reason, session_turns = await _run_inner_loop(
                client=client, mcp=mcp, tool_defs=tool_defs,
                messages=messages, state_dir=state_dir,
                logger=logger, args=args, deadline=deadline,
            )
            last_session_turn = session_turns
            # Carry an advisory note into the next session (cuts re-derivation tax).
            session_note = _build_session_note(state_dir, reason)
            last_reason = reason

            info(f"Session {session_n} complete: {session_turns} turns, reason={reason}")
            log_session_end(logger, session_turns, reason)
            logger.close()

            # Loop only on natural session rollover. Anything else exits.
            if reason != "context_overflow":
                break
    except KeyboardInterrupt:
        info(f"\nInterrupted after {last_session_turn} turns in last session.")
        last_reason = "interrupted"
        # Emit a session_end record on the currently-open log if any.
        if logger.fh is not None:
            log_session_end(logger, last_session_turn, "interrupted")
            logger.close()
    finally:
        if mcp:
            try:
                await mcp.close()
            except Exception as e:
                info(f"WARN: MCP close failed: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Kaetram Qwen agent harness — long-lived warm-session loop. "
            "MCP server, browser, and login persist across context-overflow "
            "rollovers; only the conversation resets."
        )
    )
    endpoint = parser.add_mutually_exclusive_group(required=True)
    endpoint.add_argument("--endpoint", help="OpenAI-compatible API base URL")
    endpoint.add_argument(
        "--endpoint-env",
        help="Environment variable containing the API base URL (keeps signed URLs out of argv/logs)",
    )
    parser.add_argument("--model", default="kaetram", help="Model name")
    parser.add_argument("--api-key", default=None, help="API key (default: not-needed)")
    parser.add_argument(
        "--inference-seed", type=int,
        help="Base seed; each request derives a deterministic session/turn sampling seed",
    )
    parser.add_argument("--system-prompt", default=None, help="System prompt file or text")
    parser.add_argument("--sandbox", default="/tmp/kaetram_agent_4",
                        help="Sandbox directory (for state/, game_state.json, .session_counter)")
    parser.add_argument("--run-dir", default=None,
                        help="Directory to write session_<N>_<TS>.log and sidecar meta files. "
                             "Default: <sandbox>/logs (back-compat for solo dev).")
    parser.add_argument("--harness-meta", default=None,
                        help="Optional JSON file with sidecar-template fields "
                             "(agent_id, harness, model, username, auth_mode, max_budget_usd). "
                             "Merged into each session's meta sidecar.")
    parser.add_argument("--max-duration-seconds", type=int, default=0,
                        help="Wall-clock cap (eval-only). 0 = unbounded. "
                             "Orchestrate uses external SIGTERM via --hours instead.")
    parser.add_argument("--server-port", default="", help="Game server WebSocket port (e.g. 9031)")
    parser.add_argument("--project-dir", default=os.path.dirname(os.path.abspath(__file__)),
                        help="Project directory (for mcp_game_server.py)")
    parser.add_argument("--personality", default="completionist",
                        choices=["grinder", "completionist", "explorer_tinkerer", "none"],
                        help="Personality block (must match training); shell handles substitution.")
    parser.add_argument(
        "--tool-schema-source",
        choices=["live", "canonical"],
        default=os.environ.get("KAETRAM_TOOL_SCHEMA_SOURCE", "live"),
        help=(
            "Tool schema sent to the model: live preserves historical r10/OPD "
            "behavior; canonical is required by native_tools_v1 checkpoints"
        ),
    )
    args = parser.parse_args()
    if args.inference_seed is not None:
        try:
            validate_inference_seed(args.inference_seed)
        except ValueError as exc:
            parser.error(str(exc))
    asyncio.run(run_agent(args))


if __name__ == "__main__":
    main()
