"""Canonicalize malformed Qwen tool-call wire syntax to the template's canonical form.

The 2b-opd policies emit three malformed surface-form families (counts from the
r2 eval run_20260612_044933):
  (1) kwarg-in-key:        <parameter=accept_quest_offer=True> ... </parameter>   (86x)
  (2) call-syntax-in-tag:  <function=gather("Oak")> / <function=query_quest(quest_name="X")>  (599x)
  (3) corrupted close tag: <parameter=slot>\n3\n</number>  (close-tag word leakage)

Canonical wire form (what the chat template serializes and the parser accepts):
  <function=NAME>
  <parameter=KEY>
  VALUE
  </parameter>
  </function>

Used by the round-3 data build for counterfactual-canonicalized teacher grading:
malformed calls in the HISTORY are rewritten so the teacher grades the student's
emission under a clean-convention context (the copy prior has nothing to copy);
the student/behavior side always scores the real context. `canonicalize_text`
returns None when any malformed-looking construct cannot be rewritten with
confidence — the caller falls back to round-2 abstention masking for that record.
"""
from __future__ import annotations

import re

# Positional parameter order per tool, from the MCP signatures
# (mcp_server/tools/*.py — ctx excluded). Single source for mapping
# call-syntax positional args onto canonical <parameter=...> keys.
POSITIONAL_PARAMS: dict[str, list[str]] = {
    "attack": ["mob_name"],
    "set_attack_style": ["style"],
    "craft_item": ["skill", "recipe_key", "count"],
    "gather": ["resource_name"],
    "eat_food": ["slot"],
    "drop_item": ["slot"],
    "equip_item": ["slot"],
    "navigate": ["x", "y"],
    "warp": ["location"],
    "interact_npc": ["npc_name", "expect", "include_ui_state", "accept_quest_offer"],
    "query_quest": ["quest_name"],
    "buy_item": ["npc_name", "item_index", "count"],
    "observe": [],
    "loot": [],
    "respawn": [],
    "stuck_reset": [],
    "cancel_nav": [],
}

# (1) kwarg written into the parameter key.
KWARG_IN_KEY_RE = re.compile(
    r"<parameter=([A-Za-z_]\w*)=([^>\n]*)>(\s*\n?)(.*?)(\n?\s*)</parameter>",
    re.DOTALL)
# Bare form with no body/closing pair on the same element:
KWARG_IN_KEY_BARE_RE = re.compile(r"<parameter=([A-Za-z_]\w*)=([^>\n]*)>")

# (2) Python-call syntax inside the function tag (tolerate trailing space before '>').
CALL_IN_TAG_RE = re.compile(r"<function=([A-Za-z_]\w*)\(([^>\n]*)\)\s*>")

# (3) corrupted close tag directly after a parameter body: anything that is not
# a structural close. Only fixed in the unambiguous shape
#   <parameter=K>\nVALUE\n</WORD>   followed by another <parameter= or </function>
CORRUPT_CLOSE_RE = re.compile(
    r"(<parameter=[A-Za-z_]\w*>\s*\n[^<]*?)\n?</(?!parameter>|function>|tool_call>)"
    r"[A-Za-z_]*\n?>?(\s*\n(?:<parameter=|</function>))")

# Detector for "any malformed construct present" — shared with the build.
MALFORMED_ANY_RE = re.compile(
    r"<parameter=[^>\n]*=[^>\n]*>|<function=[A-Za-z_]\w*\(")

# Python-style call literals in the system prompt's tool docs — the measured
# copy-prior prime (flip probe June 12: rewriting these in the TEACHER's
# grading copy suppresses its endorsement of malformed continuations by a
# median -1.21 nats on 86% of flagged states; history-only canonicalization
# measured null). Student/serving/eval copies are never touched.
DOC_LITERAL_RE = re.compile(r"\b([a-z_]+)\(([^()]*)\)")


def docify_system_prompt(text: str) -> str:
    """Reshape Python-call doc literals into non-call prose for the teacher's
    grading context: `interact_npc(npc_name, accept_quest_offer=False)` ->
    `interact_npc [params: npc_name, accept_quest_offer default False]`."""
    def sub(m):
        name, args = m.group(1), m.group(2)
        if not args.strip():
            return name
        return f"{name} [params: {args.replace('=', ' default ')}]"
    return DOC_LITERAL_RE.sub(sub, text)


def is_malformed(text: str) -> bool:
    return bool(MALFORMED_ANY_RE.search(text or ""))


def _split_call_args(argstr: str):
    """Split 'a, b, k=v' respecting quotes. Returns list of raw arg strings."""
    args, buf, depth, quote = [], [], 0, None
    for ch in argstr:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([{":
            depth += 1; buf.append(ch)
        elif ch in ")]}":
            depth -= 1; buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip()); buf = []
        else:
            buf.append(ch)
    if quote is not None:
        return None  # unterminated quote — not confidently parseable
    tail = "".join(buf).strip()
    if tail:
        args.append(tail)
    return args


def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _render_params(pairs) -> str:
    out = []
    for k, v in pairs:
        out.append(f"<parameter={k}>\n{v}\n</parameter>\n")
    return "".join(out)


def _fix_call_in_tag(m: re.Match):
    """<function=name(args)> -> '<function=name>\n<parameter=..>..' or None."""
    name, argstr = m.group(1), m.group(2)
    if name not in POSITIONAL_PARAMS:
        return None
    argstr = argstr.strip()
    if not argstr:
        return f"<function={name}>\n"
    raw_args = _split_call_args(argstr)
    if raw_args is None:
        return None
    pairs = []
    pos_i = 0
    order = POSITIONAL_PARAMS[name]
    for a in raw_args:
        kw = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", a, re.DOTALL)
        if kw:
            pairs.append((kw.group(1), _strip_quotes(kw.group(2))))
        else:
            if pos_i >= len(order):
                return None  # more positionals than the tool takes
            pairs.append((order[pos_i], _strip_quotes(a)))
            pos_i += 1
    return f"<function={name}>\n" + _render_params(pairs)


# --- Harness-side recovery: extract EXECUTABLE (name, args) from content the
# server's tool parser dropped (KAETRAM_TOOL_RECOVERY). The model emits the
# malformed call in its text channel; we recover it so the harness can execute
# it instead of letting the unanswered call spam-loop. ---
_INT_PARAMS = frozenset({"x", "y", "slot", "count", "item_index"})
_BOOL_PARAMS = frozenset({"accept_quest_offer", "include_ui_state"})
_CANON_FUNC_RE = re.compile(r"<function=([A-Za-z_]\w*)>\n?(.*?)</function>", re.DOTALL)
_CANON_PARAM_RE = re.compile(
    r"<parameter=([A-Za-z_]\w*)(?:=([^>\n]*))?>\n?(.*?)\n?</parameter>", re.DOTALL)


def _coerce(key: str, val: str):
    v = _strip_quotes(val)
    if key in _INT_PARAMS:
        try:
            return int(v)
        except (ValueError, TypeError):
            return v
    if key in _BOOL_PARAMS:
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
    return v


def recover_tool_calls(content: str):
    """Best-effort recovery of executable tool calls from malformed content.

    Returns a list of {"name", "args"} for any tool call the server could not
    parse (call-syntax-in-tag `<function=gather("Oak")>` or a canonical
    `<function=NAME>...<parameter=..>` block with kwarg-in-key params). Empty
    list if nothing recoverable. Used only when the server returned no
    structured tool_calls — never overrides a clean parse.
    """
    if not content:
        return []
    calls = []
    # Form A: call-syntax inside the function tag.
    for m in CALL_IN_TAG_RE.finditer(content):
        name, argstr = m.group(1), m.group(2).strip()
        if name not in POSITIONAL_PARAMS:
            continue
        args = {}
        if argstr:
            raw = _split_call_args(argstr)
            if raw is None:
                continue
            order = POSITIONAL_PARAMS[name]
            pos_i = 0
            ok = True
            for a in raw:
                kw = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", a, re.DOTALL)
                if kw:
                    args[kw.group(1)] = _coerce(kw.group(1), kw.group(2))
                elif pos_i < len(order):
                    args[order[pos_i]] = _coerce(order[pos_i], a)
                    pos_i += 1
                else:
                    ok = False
                    break
            if not ok:
                continue
        calls.append({"name": name, "args": args})
    if calls:
        return calls
    # Form B: canonical (or kwarg-in-key) <function=NAME> ... </function> that
    # the server still failed to parse (e.g. corrupted close tag earlier).
    for m in _CANON_FUNC_RE.finditer(content):
        head = m.group(0).split(">", 1)[0]
        if "(" in head:
            continue  # call-syntax — Form A already handled it
        name, body = m.group(1), m.group(2)
        if name not in POSITIONAL_PARAMS:
            continue
        args = {}
        for pm in _CANON_PARAM_RE.finditer(body):
            k, vk, vb = pm.group(1), pm.group(2), pm.group(3).strip()
            args[k] = _coerce(k, vb if vb else (vk or ""))
        calls.append({"name": name, "args": args})
    return calls


def canonicalize_text(text: str):
    """Rewrite malformed tool-call syntax in `text` to canonical wire form.

    Returns (canonical_text, n_rewrites) — n_rewrites == 0 means the text was
    already canonical (idempotent). Returns None when a malformed-looking
    construct exists that cannot be rewritten with confidence; callers fall
    back to abstention masking.
    """
    if text is None:
        return None
    n = 0
    out = text

    # (3) corrupted close tags first (their bodies may contain forms (1)/(2)).
    while True:
        m = CORRUPT_CLOSE_RE.search(out)
        if not m:
            break
        out = out[:m.start()] + m.group(1) + "\n</parameter>" + m.group(2) + out[m.end():]
        n += 1

    # (1) kwarg-in-key, paired form: body may duplicate the value or be empty.
    def fix_kwarg(m: re.Match):
        nonlocal n
        key, val_in_key, body = m.group(1), m.group(2).strip(), m.group(4).strip()
        value = body if body else _strip_quotes(val_in_key)
        n += 1
        return f"<parameter={key}>\n{value}\n</parameter>"
    out = KWARG_IN_KEY_RE.sub(fix_kwarg, out)
    # Bare leftovers (no matching </parameter> captured) — rewrite tag + inject value.
    def fix_kwarg_bare(m: re.Match):
        nonlocal n
        key, val = m.group(1), _strip_quotes(m.group(2).strip())
        n += 1
        return f"<parameter={key}>\n{val}\n</parameter>"
    out = KWARG_IN_KEY_BARE_RE.sub(fix_kwarg_bare, out)

    # (2) call-syntax inside the function tag.
    failed = False
    def fix_call(m: re.Match):
        nonlocal n, failed
        rep = _fix_call_in_tag(m)
        if rep is None:
            failed = True
            return m.group(0)
        n += 1
        return rep
    out = CALL_IN_TAG_RE.sub(fix_call, out)
    if failed:
        return None

    # Confidence check: nothing malformed-looking may remain.
    if is_malformed(out):
        return None
    return out, n
