"""Shared parsers for Claude harness session logs (JSONL stream-json).

Log shape:

    types: system (1, line 1) | assistant | user | rate_limit_event | result (1, last)

    assistant.message.content[]: thinking | text | tool_use
    user.message.content[]:      tool_result   (paired by tool_use_id)

    tool_result.content is a STRING containing:
        '{"result": "<INNER STRING>"}'

    For observe(), INNER STRING is:
        '<game_state_json>\\n\\nASCII_MAP:<ascii grid>'

    For other tools, INNER STRING is just the tool's JSON output (also stringified).

    So a full decode of an observe result is THREE json.loads + one split.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[2]
RAW_DIR = REPO / "dataset" / "raw"

# Malformed tool-call syntax emitted as plain text (server parser drops it):
# `<function=name(args)>` Python-call form, or `<parameter=key=value>` kwarg-in-key.
_MALFORMED_EMIT_RE = re.compile(r"<function=[A-Za-z_]\w*\(|<parameter=[^>\n]*=[^>\n]*>")

# orchestrate.py writes timestamps in EDT (UTC-4) — keep that in sync.
_EST = timezone(timedelta(hours=-4))


# ── Discovery ────────────────────────────────────────────────────────────────

def list_agent_dirs() -> list[Path]:
    return sorted(p for p in RAW_DIR.glob("agent_*") if p.is_dir())


def list_runs(agent_dir: Path) -> list[Path]:
    """Every `runs/run_*/` dir for an agent, sorted by run_id (chronological)."""
    runs_root = agent_dir / "runs"
    if not runs_root.is_dir():
        return []
    return sorted(p for p in runs_root.glob("run_*") if p.is_dir())


def latest_run(agent_dir: Path) -> Path | None:
    """Most recent run dir (resolves the `logs/` symlink if present)."""
    sym = agent_dir / "logs"
    if sym.is_symlink():
        try:
            target = sym.resolve()
            if target.is_dir():
                return target
        except OSError:
            pass
    runs = list_runs(agent_dir)
    return runs[-1] if runs else None


def latest_session_log(agent_dir: Path) -> Path | None:
    """Most recently modified .log under the agent's latest run."""
    run = latest_run(agent_dir)
    if not run:
        return None
    logs = list(run.glob("session_*.log"))
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def latest_logs_per_agent() -> list[tuple[Path, Path]]:
    """[(agent_dir, log_path)] for each agent that has at least one log."""
    out = []
    for d in list_agent_dirs():
        log = latest_session_log(d)
        if log:
            out.append((d, log))
    return out


def session_meta(log_path: Path) -> dict:
    meta = log_path.with_suffix(".meta.json")
    return json.loads(meta.read_text()) if meta.exists() else {}


def run_meta(run_dir: Path) -> dict:
    f = run_dir / "run.meta.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return {}


# ── Time helpers ─────────────────────────────────────────────────────────────

def parse_session_timestamp(log_path: Path) -> datetime | None:
    """Pull the timestamp from a session log's filename (session_N_YYYYMMDD_HHMMSS.log).

    Returned in EDT — orchestrate.py writes session timestamps in local time
    (the VM's TZ). We tag them as EDT for display consistency with run_id.
    """
    m = re.search(r"session_\d+_(\d{8})_(\d{6})", log_path.name)
    if not m:
        return None
    try:
        ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        return ts.replace(tzinfo=_EST)
    except ValueError:
        return None


def fmt_est(dt: datetime | None) -> str:
    """12-hour AM/PM EST timestamp, e.g. `YYYY-MM-DD HH:MM AM/PM EST`."""
    if dt is None:
        return "?"
    return dt.astimezone(_EST).strftime("%Y-%m-%d %I:%M %p EST")


def fmt_duration(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return "?"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


# ── Decoders ─────────────────────────────────────────────────────────────────

_FORMAT_NOTE_PREFIX = "[format]"


def _strip_format_note(raw: str) -> str:
    """Drop a leading harness format-recovery note (KAETRAM_TOOL_RECOVERY:
    `[format] ...one paragraph...\\n\\n<real result>`). The note precedes the
    genuine MCP result on recovered tool calls; without stripping it the result
    is opaque to every decoder (it fails json.loads / the ASCII partition)."""
    if isinstance(raw, str) and raw.startswith(_FORMAT_NOTE_PREFIX):
        _, sep, rest = raw.partition("\n\n")
        if sep:
            return rest
    return raw


def decode_tool_result_content(raw: str) -> tuple[dict | str, str | None]:
    """Decode a tool_result `.content` string.

    Handles two wire formats — same MCP, same payload, different harness wrappers:
      * Claude Code CLI:  '{"result": "<inner>", "is_error": false}'
      * Qwen / direct MCP: '<inner>' (bare; play_qwen logs raw MCP output)

    `<inner>` is `<JSON>` for non-observe tools, or `<JSON>\\n\\nASCII_MAP:<grid>`
    for observe. A leading `[format]` recovery note (if present) is stripped
    first. Returns (payload, ascii_map). Falls back to (raw, None) if nothing
    decodes.
    """
    raw = _strip_format_note(raw)
    inner: str | None = None
    try:
        wrapper = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        wrapper = None
    if isinstance(wrapper, dict) and isinstance(wrapper.get("result"), str):
        # Claude wrapper.
        inner = wrapper["result"]
    elif isinstance(wrapper, dict) and "pos" not in wrapper and "ASCII_MAP" not in raw:
        # Qwen non-observe path: raw is already the inner JSON object (no
        # wrapper, no ASCII suffix). Return as-is.
        return wrapper, None
    else:
        # Qwen observe (or any bare-string format with trailing ASCII_MAP):
        # `json.loads(raw)` either failed with "Extra data" or returned a dict
        # that itself contains the game-state keys. Treat raw as inner.
        inner = raw
    body, sep, ascii_map = inner.partition("\n\nASCII_MAP:")
    if not sep:
        # Compacted observe (KAETRAM_OBSERVE_COMPACT): no ASCII_MAP, but the
        # body still carries a trailing STUCK_CHECK — strip it for the JSON.
        body = inner.partition("\n\nSTUCK_CHECK:")[0]
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return inner, None
    return payload, (ascii_map if sep else None)


def decode_kaetram_tool_output(raw: str) -> tuple[dict | str, str | None]:
    """Decode an opencode-style tool output string (no `{"result": ...}` wrapper).

    Kaetram tools return JSON; observe additionally suffixes `\\n\\nASCII_MAP:<grid>`.
    Returns (payload, ascii_map). Falls back to (raw, None) if JSON decode fails.
    """
    if not isinstance(raw, str):
        return raw, None
    raw = _strip_format_note(raw)
    body, sep, ascii_map = raw.partition("\n\nASCII_MAP:")
    if not sep:
        # Compacted observe: no ASCII_MAP; strip the trailing STUCK_CHECK.
        body = raw.partition("\n\nSTUCK_CHECK:")[0]
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return raw, None
    return payload, (ascii_map if sep else None)


# ── Iteration ────────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    idx: int                   # ordinal position in the session (0-based)
    line_no: int               # 1-based line number in the log
    name: str                  # e.g. "mcp__kaetram__gather"
    short_name: str            # "gather"
    input: dict
    tool_use_id: str
    thinking: str | None = None    # from the same assistant turn, if any
    text: str | None = None        # ditto
    result_payload: object | None = None
    result_ascii_map: str | None = None
    result_error: str | None = None
    result_raw: str | None = None  # if decode failed

    @property
    def is_observe(self) -> bool:
        return self.short_name == "observe"

    @property
    def is_error(self) -> bool:
        if self.result_error:
            return True
        if isinstance(self.result_payload, dict):
            return bool(self.result_payload.get("error"))
        # MCP schema/validation failures come back as plain strings
        # ("Error executing tool gather: 1 validation error ..."), not JSON —
        # without this branch the largest qwen failure class is invisible.
        if isinstance(self.result_payload, str) and self.result_payload.startswith(
                "Error executing tool"):
            return True
        return False


@dataclass
class SessionView:
    log_path: Path
    meta: dict = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)
    rate_limit_events: int = 0
    result_summary: dict | None = None
    init_info: dict | None = None
    n_assistant: int = 0
    n_user: int = 0
    n_thinking: int = 0
    n_text: int = 0
    # Assistant text blocks carrying malformed tool-call syntax the server's
    # parser dropped (`<function=name(...)>` / `<parameter=k=v>`). These never
    # become tool_use records, so without this counter they are invisible to
    # every tool-call metric — the blind spot that hid the r3 spam paralysis.
    n_malformed_emit: int = 0
    # Cost + token aggregation. Populated for both harnesses (claude reads
    # from the final `result` event; opencode aggregates per-step
    # `step_finish` parts since it never emits a final summary). None when
    # unknown — keep distinct from 0.0 so callers can tell.
    total_cost_usd: float | None = None
    total_tokens: dict = field(default_factory=dict)

    @property
    def num_turns(self) -> int:
        """Best-effort turn count: prefer result_summary.num_turns when the
        session ended cleanly (claude); otherwise fall back to n_assistant
        which is a reasonable proxy. Never returns 0 if any tool call ran."""
        rs = self.result_summary or {}
        n = rs.get("num_turns")
        if isinstance(n, int) and n > 0:
            return n
        return max(self.n_assistant, len(self.tool_calls) and 1)


def iter_lines(log_path: Path) -> Iterable[tuple[int, dict]]:
    with log_path.open() as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_session(log_path: Path) -> SessionView:
    """Walk a session log once and produce a structured view.

    Pairs assistant tool_use blocks with the matching user tool_result by id.
    Decodes each tool_result. Carries thinking/text from the same assistant
    turn onto the tool call (often multiple tool_use per assistant turn — we
    attach the thinking/text to ALL of them for simplicity).
    """
    sv = SessionView(log_path=log_path, meta=session_meta(log_path))
    pending: dict[str, ToolCall] = {}    # tool_use_id -> ToolCall
    idx = 0
    # Each assistant message holds ONE block type (thinking | text | tool_use),
    # so we track the most-recent thinking/text across messages and consume
    # them when a tool_use arrives.
    pending_thinking: str | None = None
    pending_text: str | None = None

    for line_no, rec in iter_lines(log_path):
        rtype = rec.get("type")
        if rtype == "system" and rec.get("subtype") == "init":
            sv.init_info = {
                k: rec.get(k) for k in ("model", "session_id", "tools", "mcp_servers")
            }
            continue
        if rtype == "rate_limit_event":
            # Claude Code emits one of these per API call as a quota-tracking
            # header. status="allowed" is informational (not a throttle) and
            # would otherwise drown the count. Only tally events that
            # actually signal throttling or warning-level utilization.
            info = rec.get("rate_limit_info") or {}
            if info.get("status") in ("allowed_warning", "rate_limited", "blocked"):
                sv.rate_limit_events += 1
            continue
        if rtype == "result":
            sv.result_summary = {
                k: rec.get(k) for k in (
                    "total_cost_usd", "num_turns", "duration_ms",
                    "duration_api_ms", "is_error", "stop_reason",
                    "terminal_reason", "usage", "modelUsage",
                )
            }
            cost = rec.get("total_cost_usd")
            if isinstance(cost, (int, float)):
                sv.total_cost_usd = float(cost)
            usage = rec.get("usage") or {}
            if isinstance(usage, dict):
                sv.total_tokens = {
                    k: usage.get(k, 0) for k in (
                        "input_tokens", "output_tokens",
                        "cache_creation_input_tokens", "cache_read_input_tokens",
                    ) if usage.get(k) is not None
                }
            continue
        if rtype == "assistant":
            sv.n_assistant += 1
            content = rec.get("message", {}).get("content", []) or []
            for blk in content:
                btype = blk.get("type")
                if btype == "thinking":
                    sv.n_thinking += 1
                    pending_thinking = blk.get("thinking")
                elif btype == "text":
                    sv.n_text += 1
                    pending_text = blk.get("text")
                    if pending_text and _MALFORMED_EMIT_RE.search(pending_text):
                        sv.n_malformed_emit += 1
                elif btype == "tool_use":
                    name = blk.get("name", "")
                    short = name.split("__")[-1] if "__" in name else name
                    tc = ToolCall(
                        idx=idx,
                        line_no=line_no,
                        name=name,
                        short_name=short,
                        input=blk.get("input", {}) or {},
                        tool_use_id=blk.get("id", ""),
                        thinking=pending_thinking,
                        text=pending_text,
                    )
                    idx += 1
                    if tc.tool_use_id:
                        pending[tc.tool_use_id] = tc
                    sv.tool_calls.append(tc)
                    pending_thinking = None
                    pending_text = None
            continue
        if rtype == "user":
            sv.n_user += 1
            content = rec.get("message", {}).get("content", []) or []
            for blk in content:
                if blk.get("type") != "tool_result":
                    continue
                tid = blk.get("tool_use_id")
                tc = pending.pop(tid, None) if tid else None
                if not tc:
                    continue
                raw = blk.get("content")
                if isinstance(raw, list):
                    raw = "".join(
                        x.get("text", "") for x in raw if isinstance(x, dict)
                    )
                if not isinstance(raw, str):
                    raw = json.dumps(raw)
                tc.result_raw = raw
                payload, ascii_map = decode_tool_result_content(raw)
                tc.result_ascii_map = ascii_map
                if isinstance(payload, dict):
                    tc.result_payload = payload
                    if payload.get("error"):
                        tc.result_error = str(payload["error"])[:200]
                else:
                    tc.result_payload = payload  # may be str if decode failed
            continue

    return sv


# ── OpenCode harness parser ──────────────────────────────────────────────────
#
# OpenCode JSONL shape:
#
#   types: text | reasoning | tool_use | step_start | step_finish
#
#   text:     part = {type:"text", text:<spoken text + maybe wrapped <think>...
#                 </think> reasoning emitted by the SSE-rewriting proxy>}
#             — DeepSeek V4 / NVIDIA Qwen route through scripts/nim_proxy.py,
#               which merges `delta.reasoning_content` into `delta.content`
#               wrapped in <think>...</think> tags (opencode 1.14.29's
#               openai-compatible provider doesn't read reasoning_content
#               directly). We split out the wrapped CoT into n_thinking and
#               leave the surrounding prose in n_text.
#   reasoning: part = {type:"reasoning", text:<chain-of-thought>}
#             — emitted by providers configured for interleaved reasoning
#               (DeepSeek V4 + interleaved.reasoning_content). Pure thinking;
#               increments n_thinking only.
#   tool_use: part = {type:"tool", tool:<short_name>, callID, state:{
#                 status:"completed"|"error", input, output|error, time}}
#             — `output` is the raw kaetram tool output string (no {"result":...}
#               wrapper). For observe, suffixed with "\n\nASCII_MAP:<grid>".
#   step_finish: part = {type:"step-finish", cost:<usd>, tokens:{
#                 input, output, reasoning, cache:{write, read}}}
#             — emitted at the end of every model step. Accumulate cost and
#               tokens across steps so we have a real total_cost_usd at end
#               of session (opencode never emits a final result event).
#   step_start: ignored.
#
# No rate_limit_event lines, no `result` summary line — we synthesize a
# `result_summary` at end-of-parse so downstream code (analyze.py, eval, …)
# doesn't have to special-case None.

_OPENCODE_THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _split_think_tags(text: str) -> tuple[str | None, str | None]:
    """Split a text blob into (thinking, prose). Either may be None.

    Mirrors `dashboard/parsers.py:170-211`: grabs every <think>...</think>
    block as thinking and concatenates the surrounding prose. Order of
    occurrence is preserved within each bucket but the buckets are
    independent — fine for accounting purposes.
    """
    if not text:
        return None, None
    thinks: list[str] = []
    prose: list[str] = []
    last_end = 0
    for m in _OPENCODE_THINK_PATTERN.finditer(text):
        pre = text[last_end:m.start()].strip()
        if pre:
            prose.append(pre)
        cot = m.group(1).strip()
        if cot:
            thinks.append(cot)
        last_end = m.end()
    tail = text[last_end:].strip()
    # Tail may be an unclosed <think> stream (the proxy normally closes them
    # on stream end via _finalize_think — defensive).
    if tail.startswith("<think>"):
        thinks.append(tail[len("<think>"):])
    elif tail:
        prose.append(tail)
    return ("\n\n".join(thinks) or None), ("\n\n".join(prose) or None)


def parse_session_opencode(log_path: Path) -> SessionView:
    sv = SessionView(log_path=log_path, meta=session_meta(log_path))
    # Seed init_info from the per-session meta.json since opencode logs have
    # no `system.init` line. Lets cmd_metrics / dashboard read model + harness
    # uniformly across harnesses.
    if sv.meta:
        sv.init_info = {
            "model": sv.meta.get("model"),
            "harness": sv.meta.get("harness", "opencode"),
            "session_id": sv.meta.get("session_id"),
            "agent_id": sv.meta.get("agent_id"),
        }
    idx = 0
    pending_thinking: str | None = None
    pending_text: str | None = None
    cost_total = 0.0
    tokens_total: dict[str, int] = {}
    saw_step_finish = False

    def _add_tokens(d: dict):
        for k, v in d.items():
            if isinstance(v, dict):
                # cache: {read, write} → flatten to cache_read_tokens etc.
                for sk, sv2 in v.items():
                    if isinstance(sv2, (int, float)):
                        tokens_total[f"cache_{sk}_tokens"] = (
                            tokens_total.get(f"cache_{sk}_tokens", 0) + int(sv2)
                        )
            elif isinstance(v, (int, float)):
                tokens_total[f"{k}_tokens"] = (
                    tokens_total.get(f"{k}_tokens", 0) + int(v)
                )

    for line_no, rec in iter_lines(log_path):
        rtype = rec.get("type")
        if rtype == "text":
            txt = (rec.get("part") or {}).get("text") or ""
            think, prose = _split_think_tags(txt)
            if think:
                sv.n_thinking += 1
                pending_thinking = think
            if prose:
                sv.n_text += 1
                pending_text = prose
            # An assistant text event happens on every model turn that emits
            # any prose-or-CoT, even if it ends up all-thinking. Count it as
            # an assistant turn so n_assistant is a usable turn proxy when
            # result_summary.num_turns is absent.
            if think or prose:
                sv.n_assistant += 1
            continue
        if rtype == "reasoning":
            # Pure CoT stream from interleaved-reasoning providers; no prose.
            txt = (rec.get("part") or {}).get("text") or ""
            if txt:
                sv.n_thinking += 1
                sv.n_assistant += 1
                pending_thinking = txt if not pending_thinking else (pending_thinking + "\n\n" + txt)
            continue
        if rtype == "tool_use":
            sv.n_user += 1  # treat tool turn as a user-side resolution boundary
            part = rec.get("part") or {}
            state = part.get("state") or {}
            full = part.get("tool", "") or ""
            short = full.split("kaetram_", 1)[-1] if full.startswith("kaetram_") else full
            tc = ToolCall(
                idx=idx,
                line_no=line_no,
                name=full,
                short_name=short,
                input=state.get("input") or {},
                tool_use_id=part.get("callID", ""),
                thinking=pending_thinking,
                text=pending_text,
            )
            idx += 1
            status = state.get("status")
            if status == "error":
                tc.result_error = str(state.get("error", ""))[:200]
            else:
                raw = state.get("output")
                if isinstance(raw, str):
                    tc.result_raw = raw
                    payload, ascii_map = decode_kaetram_tool_output(raw)
                    tc.result_ascii_map = ascii_map
                    tc.result_payload = payload
                    if isinstance(payload, dict) and payload.get("error"):
                        tc.result_error = str(payload["error"])[:200]
            sv.tool_calls.append(tc)
            pending_thinking = None
            pending_text = None
            continue
        if rtype == "step_finish":
            # opencode 1.14.29 emits dashes in the part type; the event type
            # itself uses underscores. Aggregate cost + tokens here since
            # there's no final `result` event we can read at end-of-stream.
            saw_step_finish = True
            part = rec.get("part") or {}
            c = part.get("cost")
            if isinstance(c, (int, float)):
                cost_total += float(c)
            toks = part.get("tokens") or {}
            if isinstance(toks, dict):
                _add_tokens(toks)
            continue
        # step_start ignored

    if saw_step_finish:
        sv.total_cost_usd = round(cost_total, 4)
        sv.total_tokens = tokens_total
    # Synthesize result_summary so downstream code (cmd_metrics, eval, …)
    # doesn't have to special-case None for opencode logs. Mark as synthetic
    # via stop_reason="truncated" if no clean termination signal is present
    # — opencode never emits one, so this is the universal case.
    sv.result_summary = {
        "total_cost_usd": sv.total_cost_usd,
        "num_turns": max(sv.n_assistant, len(sv.tool_calls)),
        "is_error": False,
        "stop_reason": "truncated",
        "synthetic": True,
        "usage": dict(sv.total_tokens),
    }
    return sv


def parse_session_auto(log_path: Path, harness: str | None = None) -> SessionView:
    """Pick the right parser based on harness (from meta.json, or override)."""
    h = harness or session_meta(log_path).get("harness", "claude")
    if h == "opencode":
        return parse_session_opencode(log_path)
    return parse_session(log_path)


# ── Convenience extractors over a parsed session ─────────────────────────────

def latest_observe(sv: SessionView) -> dict | None:
    """Most recent successful observe payload (game state dict)."""
    for tc in reversed(sv.tool_calls):
        if tc.is_observe and isinstance(tc.result_payload, dict) and not tc.is_error:
            return tc.result_payload
    return None


def first_observe(sv: SessionView) -> dict | None:
    for tc in sv.tool_calls:
        if tc.is_observe and isinstance(tc.result_payload, dict) and not tc.is_error:
            return tc.result_payload
    return None


def tool_call_counts(sv: SessionView) -> dict[str, int]:
    out: dict[str, int] = {}
    for tc in sv.tool_calls:
        out[tc.short_name] = out.get(tc.short_name, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def tool_error_counts(sv: SessionView) -> dict[str, tuple[int, int]]:
    """short_name -> (errors, total)."""
    totals: dict[str, int] = {}
    errs: dict[str, int] = {}
    for tc in sv.tool_calls:
        totals[tc.short_name] = totals.get(tc.short_name, 0) + 1
        if tc.is_error:
            errs[tc.short_name] = errs.get(tc.short_name, 0) + 1
    return {k: (errs.get(k, 0), totals[k]) for k in totals}


def deaths(sv: SessionView) -> list[ToolCall]:
    out = []
    for tc in sv.tool_calls:
        if tc.short_name == "respawn":
            out.append(tc)
            continue
        payload = tc.result_payload
        if not isinstance(payload, dict):
            continue
        status = payload.get("status")
        if isinstance(status, dict) and status.get("dead"):
            out.append(tc)
    return out


# ── Run-level view ───────────────────────────────────────────────────────────

@dataclass
class RunView:
    """A run is one orchestrate.py launch — `runs/run_<EST_TS>/` per agent."""
    agent_dir: Path
    run_dir: Path
    meta: dict = field(default_factory=dict)
    session_paths: list[Path] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return self.meta.get("run_id") or self.run_dir.name

    @property
    def started_at(self) -> datetime | None:
        s = self.meta.get("started_at")
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    @property
    def last_activity(self) -> datetime | None:
        if not self.session_paths:
            return self.started_at
        latest_mtime = max(p.stat().st_mtime for p in self.session_paths)
        return datetime.fromtimestamp(latest_mtime, tz=_EST)

    @property
    def duration_s(self) -> float | None:
        a, b = self.started_at, self.last_activity
        if not a or not b:
            return None
        return (b - a).total_seconds()


def parse_run(agent_dir: Path, run_dir: Path) -> RunView:
    """Build a RunView for one run dir (without parsing every session log)."""
    return RunView(
        agent_dir=agent_dir,
        run_dir=run_dir,
        meta=run_meta(run_dir),
        session_paths=sorted(run_dir.glob("session_*.log")),
    )


def runs_per_agent(include_empty: bool = False) -> list[RunView]:
    """Every run for every agent, sorted by run_id."""
    out: list[RunView] = []
    for agent_dir in list_agent_dirs():
        for run_dir in list_runs(agent_dir):
            rv = parse_run(agent_dir, run_dir)
            if include_empty or rv.session_paths:
                out.append(rv)
    return out


def latest_runs_per_agent() -> list[RunView]:
    """The most recent run per agent (one entry per agent)."""
    out = []
    for agent_dir in list_agent_dirs():
        run = latest_run(agent_dir)
        if run:
            out.append(parse_run(agent_dir, run))
    return out


# ── Run-scoped multi-session view ────────────────────────────────────────────
#
# `RunView` (above) is metadata-only — fast for `cmd_runs` listing. When you
# need to actually analyze a run (status, metrics, errors, …), you want every
# session_*.log parsed and aggregated. That's `RunSessionsView`.

@dataclass
class RunSessionsView:
    """Every session in a run, parsed and aggregated.

    The shape every analyze.py subcommand consumes. Use
    `parse_run_sessions(agent_dir, run_dir)` to construct.
    """
    agent_dir: Path
    run_dir: Path
    meta: dict = field(default_factory=dict)
    sessions: list[SessionView] = field(default_factory=list)

    # ── Identity ──
    @property
    def run_id(self) -> str:
        return self.meta.get("run_id") or self.run_dir.name

    @property
    def n_sessions(self) -> int:
        return len(self.sessions)

    @property
    def harness(self) -> str:
        return self.meta.get("harness") or (
            self.sessions[0].meta.get("harness") if self.sessions else "?"
        ) or "?"

    @property
    def model(self) -> str:
        return self.meta.get("model") or (
            self.sessions[0].meta.get("model") if self.sessions else "?"
        ) or "?"

    @property
    def personality(self) -> str:
        return self.meta.get("personality") or (
            self.sessions[0].meta.get("personality") if self.sessions else "?"
        ) or "?"

    # ── Time ──
    @property
    def started_at(self) -> datetime | None:
        s = self.meta.get("started_at")
        if s:
            try:
                return datetime.fromisoformat(s)
            except ValueError:
                pass
        # Fallback: timestamp of earliest session log filename.
        if self.sessions:
            ts = parse_session_timestamp(self.sessions[0].log_path)
            if ts:
                return ts
        return None

    @property
    def last_activity(self) -> datetime | None:
        if not self.sessions:
            return self.started_at
        latest_mtime = max(s.log_path.stat().st_mtime for s in self.sessions)
        return datetime.fromtimestamp(latest_mtime, tz=_EST)

    @property
    def duration_s(self) -> float | None:
        a, b = self.started_at, self.last_activity
        if not a or not b:
            return None
        return (b - a).total_seconds()

    # ── Aggregated session content ──
    @property
    def all_tool_calls(self) -> list[ToolCall]:
        """Tool calls across every session, in chronological order. The
        ToolCall.idx field stays session-local — use the position in the
        returned list as the run-wide index if needed."""
        out: list[ToolCall] = []
        for sv in self.sessions:
            out.extend(sv.tool_calls)
        return out

    @property
    def total_turns(self) -> int:
        """Sum of per-session num_turns. Uses each session's
        result_summary.num_turns when present; otherwise n_assistant proxy.
        """
        return sum(sv.num_turns for sv in self.sessions)

    @property
    def total_tool_calls(self) -> int:
        return sum(len(sv.tool_calls) for sv in self.sessions)

    @property
    def total_cost_usd(self) -> float | None:
        """Sum of per-session total_cost_usd. None if no session reported a
        cost (i.e. all sessions truncated and not opencode)."""
        costs = [sv.total_cost_usd for sv in self.sessions if sv.total_cost_usd is not None]
        if not costs:
            return None
        return round(sum(costs), 4)

    @property
    def total_tokens(self) -> dict:
        """Sum of per-session token counts, key-wise."""
        out: dict[str, int] = {}
        for sv in self.sessions:
            for k, v in (sv.total_tokens or {}).items():
                if isinstance(v, (int, float)):
                    out[k] = out.get(k, 0) + int(v)
        return out

    @property
    def n_thinking(self) -> int:
        return sum(sv.n_thinking for sv in self.sessions)

    @property
    def n_text(self) -> int:
        return sum(sv.n_text for sv in self.sessions)

    @property
    def n_malformed_emit(self) -> int:
        """Malformed tool calls emitted as text (dropped by the server parser).
        Nonzero => the agent spent turns on calls that never executed unless
        KAETRAM_TOOL_RECOVERY caught them (cross-check `[format]` results)."""
        return sum(sv.n_malformed_emit for sv in self.sessions)

    @property
    def terminal_reason(self) -> str:
        """How did the run end? Reads the LAST session's result_summary.
        For opencode (always synthetic) this is "truncated". For claude,
        if any session ended cleanly, that's the reason."""
        if not self.sessions:
            return "?"
        rs = self.sessions[-1].result_summary or {}
        return rs.get("stop_reason") or rs.get("terminal_reason") or "?"

    @property
    def synthetic_summary(self) -> bool:
        """True if any session's result_summary was synthesized (opencode)."""
        return any((sv.result_summary or {}).get("synthetic") for sv in self.sessions)

    # ── Observe snapshots ──
    def first_observe_in_run(self) -> dict | None:
        """First non-error observe payload across all sessions in chronological
        order. Reflects what the agent first SAW this run."""
        for sv in self.sessions:
            ob = first_observe(sv)
            if ob is not None:
                return ob
        return None

    def last_observe_in_run(self) -> dict | None:
        """Most recent non-error observe across all sessions."""
        for sv in reversed(self.sessions):
            ob = latest_observe(sv)
            if ob is not None:
                return ob
        return None

    # ── Convenience aggregators ──
    def tool_call_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for tc in self.all_tool_calls:
            out[tc.short_name] = out.get(tc.short_name, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def tool_error_counts(self) -> dict[str, tuple[int, int]]:
        totals: dict[str, int] = {}
        errs: dict[str, int] = {}
        for tc in self.all_tool_calls:
            totals[tc.short_name] = totals.get(tc.short_name, 0) + 1
            if tc.is_error:
                errs[tc.short_name] = errs.get(tc.short_name, 0) + 1
        return {k: (errs.get(k, 0), totals[k]) for k in totals}

    def deaths(self) -> list[ToolCall]:
        out: list[ToolCall] = []
        for sv in self.sessions:
            out.extend(deaths(sv))
        return out


def parse_run_sessions(agent_dir: Path, run_dir: Path,
                       *, harness: str | None = None) -> RunSessionsView:
    """Parse every session_*.log under `run_dir`, sorted chronologically by
    filename (session_N_YYYYMMDD_HHMMSS.log). `harness` overrides auto-detect
    per session — usually unnecessary since each session's meta.json carries
    the harness."""
    rmeta = run_meta(run_dir)

    def _session_sort_key(p: Path) -> tuple[int, str]:
        # Filenames are session_<N>_<YYYYMMDD>_<HHMMSS>.log.
        # Lex sort puts session_10..session_99 before session_1..session_9,
        # which silently corrupts last_observe_in_run() and every chronological
        # consumer (status, metrics, timeline) on runs with >=10 sessions.
        # Sort by the integer index, falling back to the timestamp tail.
        stem = p.stem  # session_14_20260502_052847
        parts = stem.split("_", 2)
        try:
            idx = int(parts[1])
        except (IndexError, ValueError):
            idx = -1
        tail = parts[2] if len(parts) > 2 else ""
        return (idx, tail)

    paths = sorted(run_dir.glob("session_*.log"), key=_session_sort_key)
    sessions: list[SessionView] = []
    for p in paths:
        try:
            sessions.append(parse_session_auto(p, harness=harness))
        except Exception:
            # Skip corrupt logs rather than abort the whole run analysis.
            continue
    return RunSessionsView(
        agent_dir=agent_dir,
        run_dir=run_dir,
        meta=rmeta,
        sessions=sessions,
    )


def latest_run_sessions_per_agent(*, harness: str | None = None) -> list[RunSessionsView]:
    """Convenience: parsed multi-session view of each agent's latest run."""
    out: list[RunSessionsView] = []
    for agent_dir in list_agent_dirs():
        run = latest_run(agent_dir)
        if run:
            out.append(parse_run_sessions(agent_dir, run, harness=harness))
    return out


# ── Quest progression analysis ──────────────────────────────────────────────
#
# Stage counts come from `prompts/quest_walkthroughs.json` — the canonical
# record of every quest, kept in sync with Kaetram-Open. Loaded lazily on
# first use so consumers that don't touch quest analysis pay no startup cost.

CORE_3_QUEST_NAMES: tuple[str, ...] = (
    "Foresting",
    "Herbalist's Desperation",
    "Rick's Roll",
)

_QUEST_WALKTHROUGHS_PATH = REPO / "prompts" / "quest_walkthroughs.json"
_QUEST_STAGE_COUNTS_CACHE: dict[str, int] | None = None


def quest_stage_counts() -> dict[str, int]:
    """Return {quest_name: stage_count} from prompts/quest_walkthroughs.json.

    Cached after first call. Falls back to an empty dict if the file is
    missing or unparseable; callers should default to a sentinel (1) when a
    quest isn't found here so per-quest math doesn't break.
    """
    global _QUEST_STAGE_COUNTS_CACHE
    if _QUEST_STAGE_COUNTS_CACHE is not None:
        return _QUEST_STAGE_COUNTS_CACHE
    out: dict[str, int] = {}
    try:
        data = json.loads(_QUEST_WALKTHROUGHS_PATH.read_text())
        for name, blob in data.items():
            if isinstance(blob, dict) and isinstance(blob.get("stages"), int):
                out[name] = blob["stages"]
    except (OSError, ValueError):
        pass
    _QUEST_STAGE_COUNTS_CACHE = out
    return out


def core3_total_stage_count() -> int:
    """Sum of stages across the Core 3. Used as the denominator when scoring
    stage progression as a fraction (the maintainer's '/N stages' framing)."""
    counts = quest_stage_counts()
    return sum(counts.get(n, 0) for n in CORE_3_QUEST_NAMES)


def _active_quest_signature(active_quests: object) -> dict[str, tuple[int, int]]:
    """Reduce an `active_quests` payload to {name: (stage, sub_stage)}.

    Defensive against the two key spellings observed in the wild
    (`sub_stage` and `subStage`) and against malformed entries.
    """
    out: dict[str, tuple[int, int]] = {}
    if not isinstance(active_quests, list):
        return out
    for q in active_quests:
        if not isinstance(q, dict):
            continue
        name = q.get("name")
        if not isinstance(name, str):
            continue
        stage = q.get("stage")
        sub = q.get("sub_stage")
        if sub is None:
            sub = q.get("subStage")
        try:
            out[name] = (int(stage or 0), int(sub or 0))
        except (TypeError, ValueError):
            out[name] = (0, 0)
    return out


def _finished_quest_names(finished_quests: object) -> set[str]:
    out: set[str] = set()
    if not isinstance(finished_quests, list):
        return out
    for q in finished_quests:
        if isinstance(q, dict):
            n = q.get("name")
            if isinstance(n, str):
                out.add(n)
        elif isinstance(q, str):
            out.add(q)
    return out


@dataclass
class StageEvent:
    """One transition in a quest's lifecycle."""
    session_idx: int            # 0-based session position in the run
    tc_run_idx: int             # 0-based position in RunSessionsView.all_tool_calls
    tc_session_idx: int         # ToolCall.idx — position in its own session
    from_stage: int
    to_stage: int
    trigger_tool: str | None    # the tool call immediately preceding the transition
    trigger_args: dict | None   # its input dict (e.g. NPC name)
    thinking: str | None        # reasoning attached to the trigger tool call

    @property
    def kind(self) -> str:
        if self.to_stage == 0 and self.from_stage == 0:
            return "accept"
        if self.to_stage > self.from_stage:
            return "advance"
        return "regress"


@dataclass
class QuestProgression:
    """Per-quest timeline + diagnostics for a single agent's run."""
    quest_name: str
    stage_count: int                    # canonical from quest_walkthroughs.json
    accepted_at_run_idx: int | None = None      # first all_tool_calls idx where active
    finished_at_run_idx: int | None = None
    max_stage_reached: int = 0
    stage_events: list[StageEvent] = field(default_factory=list)
    turns_active: int = 0               # observes carrying it as active
    tools_while_active: dict[str, int] = field(default_factory=dict)
    npcs_while_active: dict[str, int] = field(default_factory=dict)
    errors_while_active: dict[str, int] = field(default_factory=dict)
    last_state: tuple[int, int] | None = None   # (stage, sub_stage) at end

    @property
    def finished(self) -> bool:
        return self.finished_at_run_idx is not None

    @property
    def stage_fraction(self) -> tuple[int, int]:
        """(reached, total) — for headline `N/M stages` reporting."""
        if self.finished:
            return (self.stage_count, self.stage_count)
        return (self.max_stage_reached, self.stage_count)


def progression_for_quests(
    rv: "RunSessionsView",
    quest_names: Iterable[str] | None = None,
) -> dict[str, QuestProgression]:
    """Build per-quest progression timelines for one run.

    Walks every tool call in chronological order. Stage transitions are
    detected by comparing successive observe payloads' `active_quests` set
    plus `finished_quests` set. The trigger tool is the most recent
    non-observe call before the transition was first observed.

    `quest_names` defaults to the Core 3. Pass a different iterable (or
    `None` and pass quest names later) to scope to other quests.
    """
    targets = list(quest_names) if quest_names is not None else list(CORE_3_QUEST_NAMES)
    counts = quest_stage_counts()
    progs: dict[str, QuestProgression] = {
        n: QuestProgression(quest_name=n, stage_count=counts.get(n, 1))
        for n in targets
    }
    target_set = set(targets)

    prev_active: dict[str, tuple[int, int]] = {}
    prev_finished: set[str] = set()
    last_non_observe: ToolCall | None = None
    last_thinking: str | None = None
    active_now: set[str] = set()       # quests whose stage_count we count time against

    all_calls = rv.all_tool_calls
    # Build session boundaries so we can map a flat index → (session_idx, ToolCall.idx).
    session_starts: list[int] = []
    cum = 0
    for sv in rv.sessions:
        session_starts.append(cum)
        cum += len(sv.tool_calls)

    def _session_idx_for_run_idx(run_idx: int) -> int:
        # Linear scan — cheap, sessions are O(10).
        s_idx = 0
        for i, start in enumerate(session_starts):
            if run_idx >= start:
                s_idx = i
            else:
                break
        return s_idx

    for run_idx, tc in enumerate(all_calls):
        # Track tool/error attribution to whatever quests are active right now.
        for qname in active_now:
            if qname not in progs:
                continue
            prog = progs[qname]
            prog.tools_while_active[tc.short_name] = (
                prog.tools_while_active.get(tc.short_name, 0) + 1
            )
            if tc.short_name == "interact_npc":
                npc = (tc.input or {}).get("npc_name") or "?"
                prog.npcs_while_active[npc] = prog.npcs_while_active.get(npc, 0) + 1
            if tc.is_error:
                cat = categorize_error(tc.result_error or "")
                prog.errors_while_active[cat] = prog.errors_while_active.get(cat, 0) + 1

        if tc.short_name == "observe":
            payload = tc.result_payload if isinstance(tc.result_payload, dict) else {}
            cur_active = _active_quest_signature(payload.get("active_quests"))
            cur_finished = _finished_quest_names(payload.get("finished_quests"))

            # Step 1: handle any quest in our target set that changed state.
            seen_targets = (set(cur_active.keys()) | cur_finished) & target_set
            seen_targets |= (set(prev_active.keys()) | prev_finished) & target_set
            for qname in seen_targets:
                prog = progs[qname]
                # First time we ever see it active.
                if qname in cur_active and prog.accepted_at_run_idx is None and qname not in prev_finished:
                    prog.accepted_at_run_idx = run_idx
                    prog.stage_events.append(StageEvent(
                        session_idx=_session_idx_for_run_idx(run_idx),
                        tc_run_idx=run_idx,
                        tc_session_idx=tc.idx,
                        from_stage=0, to_stage=cur_active[qname][0],
                        trigger_tool=last_non_observe.short_name if last_non_observe else None,
                        trigger_args=dict(last_non_observe.input or {}) if last_non_observe else None,
                        thinking=(last_non_observe.thinking if last_non_observe else last_thinking),
                    ))
                # Stage advance while active.
                if qname in prev_active and qname in cur_active:
                    prev_stage = prev_active[qname][0]
                    new_stage = cur_active[qname][0]
                    if new_stage != prev_stage:
                        prog.stage_events.append(StageEvent(
                            session_idx=_session_idx_for_run_idx(run_idx),
                            tc_run_idx=run_idx,
                            tc_session_idx=tc.idx,
                            from_stage=prev_stage, to_stage=new_stage,
                            trigger_tool=last_non_observe.short_name if last_non_observe else None,
                            trigger_args=dict(last_non_observe.input or {}) if last_non_observe else None,
                            thinking=(last_non_observe.thinking if last_non_observe else last_thinking),
                        ))
                # Quest finished (left active set AND in finished set).
                if (qname in cur_finished
                        and qname not in prev_finished
                        and prog.finished_at_run_idx is None):
                    prog.finished_at_run_idx = run_idx
                    last_known_stage = (
                        prev_active.get(qname, (prog.max_stage_reached, 0))[0]
                    )
                    prog.stage_events.append(StageEvent(
                        session_idx=_session_idx_for_run_idx(run_idx),
                        tc_run_idx=run_idx,
                        tc_session_idx=tc.idx,
                        from_stage=last_known_stage,
                        to_stage=prog.stage_count,
                        trigger_tool=last_non_observe.short_name if last_non_observe else None,
                        trigger_args=dict(last_non_observe.input or {}) if last_non_observe else None,
                        thinking=(last_non_observe.thinking if last_non_observe else last_thinking),
                    ))
                # Track max_stage_reached + last_state.
                if qname in cur_active:
                    stage, sub = cur_active[qname]
                    if stage > prog.max_stage_reached:
                        prog.max_stage_reached = stage
                    prog.last_state = (stage, sub)
                elif qname in cur_finished:
                    prog.max_stage_reached = prog.stage_count
                    prog.last_state = (prog.stage_count, 0)

            # Step 2: count "turns_active" for each currently-active target.
            for qname in cur_active:
                if qname in progs:
                    progs[qname].turns_active += 1

            # Step 3: update active_now for tool/npc/error attribution on the
            # NEXT iteration. A quest that just finished still counts its
            # finishing tool call in the previous attribution loop, but no
            # subsequent calls; so drop it from active_now here.
            active_now = {n for n in cur_active.keys() if n in target_set}

            prev_active = cur_active
            prev_finished = cur_finished
        else:
            last_non_observe = tc
            if tc.thinking:
                last_thinking = tc.thinking

    return progs


def progression_summary_line(prog: QuestProgression) -> str:
    """One-line summary for tables: `Rick's Roll  3/4  (advanced)`."""
    reached, total = prog.stage_fraction
    if prog.finished:
        tag = "FINISHED"
    elif prog.accepted_at_run_idx is None:
        tag = "untouched"
    else:
        tag = "active" if prog.last_state else "abandoned"
    return f"{prog.quest_name:<25} {reached}/{total:<3} ({tag})"


# ── Error categorization ─────────────────────────────────────────────────────

# Buckets the recurring failure modes we've observed across many runs into
# stable categories. New patterns start as OTHER and can be added explicitly.
ERROR_PATTERNS = [
    ("BFS_NO_PATH",         re.compile(r"No BFS path found", re.I)),
    ("STILL_MOVING",        re.compile(r"still moving toward", re.I)),
    ("NPC_NOT_FOUND",       re.compile(r"No NPC matching .* found nearby", re.I)),
    ("MOB_NOT_FOUND",       re.compile(r"No alive mob matching .* nearby", re.I)),
    ("STATION_UNREACHABLE", re.compile(r"(could not reach|no station found).{0,40}(station|skill)", re.I)),
    ("COMBAT_BLOCKED_WARP", re.compile(r"server blocks warp for", re.I)),
    ("HP_FULL",             re.compile(r"HP is full", re.I)),
    ("MCP_DISCONNECT",      re.compile(r"(mcp error|connection closed|not connected)", re.I)),
    ("SKILL_GATED",         re.compile(r"L\d+ required, you are L\d+", re.I)),
    ("ITEMS_NONE",          re.compile(r"items_gained.{0,5}none|no items collected", re.I)),
]


def categorize_error(text: str) -> str:
    if not text:
        return "OTHER"
    for label, rx in ERROR_PATTERNS:
        if rx.search(text):
            return label
    return "OTHER"


