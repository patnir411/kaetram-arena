"""Counterfactual flip probe: does grading-context repair suppress the teacher's
endorsement of the student's malformed tool syntax?

For r2-eval states whose EMISSION contains a malformed span, score the 4B
teacher on the SAME emission under three contexts and compare its mean logprob
on the flagged span tokens:

  real    — the context exactly as the build renders it
  hist    — history tool_calls arg-restored: bare empty-arg calls (the lossy
            parse of past malformed emissions) rebuilt from the canonicalized
            raw text, so history shows full canonical exemplars
  hist+doc— additionally, the system prompt's Python-style tool-doc literals
            (`interact_npc(npc_name, accept_quest_offer=False)`) reshaped to
            non-call prose in the TEACHER's copy only

A condition "flips" a state when the teacher's mean logprob on the flagged
tokens drops materially (default -0.2 nats) — endorsement suppressed, so the
build's advantage on those tokens turns corrective instead of reinforcing.

Usage:
  FOURB_EP=https://...modal.run/v1 python3 scripts/opd/flip_probe.py -n 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "opd"))
sys.path.insert(0, str(REPO / "finetune"))

from canonicalize import canonicalize_text, docify_system_prompt, is_malformed  # noqa: E402
from opd_probe import reconstruct_session  # noqa: E402
from opd_round1 import turn_to_chat  # noqa: E402
from render import patch_qwen_chat_template  # noqa: E402

TEACHER_EP = os.environ["FOURB_EP"].rstrip("/")
RUN_ID = "run_20260612_044933"
MAX_HIST_MSGS = 28
DROP_BAR = -0.2  # nats; median drop beyond this = condition flips

NO_ARG_OK = {"observe", "loot", "respawn", "stuck_reset", "cancel_nav",
             "set_attack_style", "warp", "interact_npc"}
CANON_CALL_RE = re.compile(
    r"<function=([A-Za-z_]\w*)>\n((?:<parameter=\w+>\n.*?\n</parameter>\n)*)</function>",
    re.DOTALL)
CANON_PARAM_RE = re.compile(r"<parameter=(\w+)>\n(.*?)\n</parameter>", re.DOTALL)


def restore_args(turn) -> dict | None:
    """turn_to_chat(turn), but with empty-arg tool_calls rebuilt from the
    canonicalized raw text. Returns None if nothing was restorable."""
    msg = turn_to_chat(turn)
    bare = [tc for tc in (msg.get("tool_calls") or [])
            if not (tc.get("function", {}).get("arguments") or {})
            and tc.get("function", {}).get("name") not in NO_ARG_OK]
    if not bare:
        return None
    canon = canonicalize_text(turn.text or "")
    if canon is None or canon[1] == 0:
        return None
    by_name = {}
    for m in CANON_CALL_RE.finditer(canon[0]):
        args = {pm.group(1): pm.group(2) for pm in CANON_PARAM_RE.finditer(m.group(2))}
        if args:
            by_name.setdefault(m.group(1), []).append(args)
    restored = 0
    for tc in bare:
        name = tc["function"]["name"]
        if by_name.get(name):
            tc["function"]["arguments"] = by_name[name].pop(0)
            restored += 1
    return msg if restored else None


def collect_probe_states(limit: int):
    """(messages_real, messages_hist, emission, has_repair) for flagged states."""
    logs = sorted((REPO / "dataset" / "raw").glob(f"agent_*/runs/{RUN_ID}/session_*.log"))
    out = []
    for lp in logs:
        try:
            base_messages, turns = reconstruct_session(lp)
        except Exception:
            continue
        rolling_real, rolling_hist = list(base_messages), list(base_messages)
        for turn, results in turns:
            if turn.tool_calls and not turn.thinking:
                emission = (turn.text or "").strip()
                if emission and is_malformed(emission):
                    def tail(r):
                        h = r[2:]
                        return r[:2] + (h[-MAX_HIST_MSGS:] if len(h) > MAX_HIST_MSGS else h)
                    repaired_differs = json.dumps(tail(rolling_hist)) != json.dumps(tail(rolling_real))
                    out.append({
                        "messages_real": tail(rolling_real),
                        "messages_hist": tail(rolling_hist),
                        "emission": emission + "<|im_end|>\n",
                        "has_repair": repaired_differs,
                        "session": lp.name, })
            real_msg = turn_to_chat(turn)
            hist_msg = restore_args(turn) or real_msg
            rolling_real.append(real_msg)
            rolling_hist.append(hist_msg)
            for tr in results:
                tool = {"role": "tool", "content": tr.result_str, "name": tr.name}
                rolling_real.append(tool); rolling_hist.append(tool)
        if len(out) >= limit * 3:
            break
    with_rep = [s for s in out if s["has_repair"]]
    without = [s for s in out if not s["has_repair"]]
    k = limit // 2
    return (with_rep[:k] + without[:limit - min(k, len(with_rep))])[:limit]


async def score(client, sem, ctx_text, full_text):
    async with sem:
        for attempt in range(4):
            try:
                r = await client.post(f"{TEACHER_EP}/score", json={
                    "context_text": ctx_text, "full_text": full_text}, timeout=300)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 400:
                    return None
            except (httpx.TimeoutException, httpx.HTTPError):
                pass
            await asyncio.sleep(3.0 * (attempt + 1))
    return None


def flagged_token_positions(tok, ctx_text, full_text):
    from canonicalize import MALFORMED_ANY_RE
    spans = []
    for m in MALFORMED_ANY_RE.finditer(full_text):
        if m.start() >= len(ctx_text):
            spans.append((m.start(), m.end()))
    if not spans:
        return None
    enc = tok(full_text, add_special_tokens=False, return_offsets_mapping=True)
    n_ctx = len(tok.encode(ctx_text, add_special_tokens=False))
    offs = enc["offset_mapping"][n_ctx:]
    return [i for i, (a, b) in enumerate(offs)
            if any(a < e and b > s for s, e in spans)]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=60)
    args = ap.parse_args()

    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-2B", trust_remote_code=True)
    patch_qwen_chat_template(tok)

    states = collect_probe_states(args.n)
    n_rep = sum(1 for s in states if s["has_repair"])
    print(f"probe states: {len(states)} ({n_rep} with repairable history)", flush=True)

    def render(msgs, emission, sysmod=False):
        if sysmod:
            msgs = [{**msgs[0], "content": docify_system_prompt(msgs[0]["content"])}] + msgs[1:]
        ctx = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        return ctx, ctx + emission

    deltas = {"hist": [], "hist+doc": []}
    deltas_rep = {"hist": [], "hist+doc": []}
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient() as client:
        for st in states:
            ctx_r, full_r = render(st["messages_real"], st["emission"])
            ctx_h, full_h = render(st["messages_hist"], st["emission"])
            ctx_d, full_d = render(st["messages_hist"], st["emission"], sysmod=True)
            pos = flagged_token_positions(tok, ctx_r, full_r)
            if not pos:
                continue
            r_r, r_h, r_d = await asyncio.gather(
                score(client, sem, ctx_r, full_r),
                score(client, sem, ctx_h, full_h),
                score(client, sem, ctx_d, full_d))
            if not (r_r and r_h and r_d):
                continue
            # boundary guard: emission must tokenize identically in every condition
            if not (r_r["target_token_ids"] == r_h["target_token_ids"] == r_d["target_token_ids"]):
                continue
            def mean_at(resp):
                vals = [resp["target_logprobs"][i] for i in pos
                        if i < len(resp["target_logprobs"]) and resp["target_logprobs"][i] is not None]
                return statistics.fmean(vals) if vals else None
            m_r, m_h, m_d = mean_at(r_r), mean_at(r_h), mean_at(r_d)
            if None in (m_r, m_h, m_d):
                continue
            deltas["hist"].append(m_h - m_r)
            deltas["hist+doc"].append(m_d - m_r)
            if st["has_repair"]:
                deltas_rep["hist"].append(m_h - m_r)
                deltas_rep["hist+doc"].append(m_d - m_r)

    print(f"\nscored {len(deltas['hist'])} states; teacher mean-logprob DELTA on flagged "
          f"emission tokens vs real ctx (negative = endorsement suppressed):")
    for cond in ("hist", "hist+doc"):
        d = deltas[cond]
        dr = deltas_rep[cond]
        if d:
            med = statistics.median(d)
            frac = sum(1 for x in d if x < DROP_BAR) / len(d)
            print(f"  {cond:<9} median {med:+.3f} nats | < {DROP_BAR} on {frac:.0%} of states"
                  f"{f' | repairable-only median {statistics.median(dr):+.3f} (n={len(dr)})' if dr else ''}")
    print(f"\nFLIP criterion: median < {DROP_BAR} nats. "
          "hist => Plan A as scoped works; only hist+doc => doc-literal decision should be revisited; "
          "neither => masking stays for these spans.")


if __name__ == "__main__":
    asyncio.run(main())
