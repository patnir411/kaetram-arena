"""Defect-origin probe: is the malformed-call defect OUR artifact?

2x2 generation-time factorial at real decision states:
  presence_penalty in {1.5 (our serving preset), 0.0 (Qwen's precise-task rec)}
  x served tool-docs in {real (our Python-style literals), canonical (docified)}

Sampled on (a) the defect-carrying r1 checkpoint — does the defect's EXPRESSION
depend on our serving artifacts? — and (b) base-2B — does our config alone SEED
variants in an untrained model? Outputs per-cell malformed rates by family
(kwarg-in-key / python-call / corrupted-close / other-is_malformed).

States: action states drawn from the base-2B baseline run (neutral contexts, no
malformed history), rendered byte-parity via reconstruct_session/turn_to_chat.

Usage:
  R1_EP=https://...-kaetram-qwen-2b-opd-inference-serve.modal.run/v1 \
  BASE_EP=https://...-kaetram-qwen-2b-inference-serve.modal.run/v1 \
  python3 scripts/opd/defect_origin_probe.py -n 20 -k 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "opd"))
sys.path.insert(0, str(REPO / "finetune"))

from canonicalize import docify_system_prompt, is_malformed  # noqa: E402
from opd_probe import reconstruct_session  # noqa: E402
from opd_round1 import turn_to_chat  # noqa: E402

BASE_RUN = "run_20260608_185339"
MAX_HIST = 28

KWARG_IN_KEY = re.compile(r"<parameter=[^>\n]*=[^>\n]*>")
PY_CALL = re.compile(r"<function=\w+\s*\(")
GOOD_CLOSE = ("</parameter>", "</function>", "</tool_call>", "</think>")
CORRUPT_CLOSE = re.compile(r"</(?!parameter>|function>|tool_call>|think>)[A-Za-z_]{0,12}>")


def classify(text: str) -> list[str]:
    fams = []
    if KWARG_IN_KEY.search(text):
        fams.append("kwarg_in_key")
    if PY_CALL.search(text):
        fams.append("python_call")
    if "<tool_call>" in text and CORRUPT_CLOSE.search(text):
        fams.append("corrupt_close")
    if not fams and is_malformed(text):
        fams.append("other_malformed")
    return fams


def collect_states(limit: int) -> list[list]:
    out = []
    logs = sorted(REPO.glob(f"dataset/raw/agent_*/runs/{BASE_RUN}/session_*.log"))
    for lp in logs[:: max(1, len(logs) // (limit * 2))]:
        try:
            base_messages, turns = reconstruct_session(lp)
        except Exception:
            continue
        rolling = list(base_messages)
        n = 0
        for turn, results in turns:
            n += 1
            if n == 4:  # a mid-session decision state with some context
                head, hist = rolling[:2], rolling[2:]
                out.append(head + hist[-MAX_HIST:])
                break
            rolling.append(turn_to_chat(turn))
            for tr in results:
                rolling.append({"role": "tool", "content": tr.result_str, "name": tr.name})
        if len(out) >= limit:
            break
    return out


async def gen(client, sem, ep, messages, pp):
    async with sem:
        for attempt in range(3):
            try:
                r = await client.post(f"{ep}/chat/completions", json={
                    "messages": messages, "max_tokens": 400,
                    "temperature": 1.0, "top_p": 0.95, "top_k": 20,
                    "presence_penalty": pp,
                }, timeout=180)
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"].get("content") or ""
            except (httpx.TimeoutException, httpx.HTTPError):
                pass
            await asyncio.sleep(2.0 * (attempt + 1))
    return None


async def run_cell(client, sem, ep, states, pp, canon_docs, k):
    total = fail = 0
    fams = Counter()
    per_sample_malformed = 0
    tasks = []
    for st in states:
        msgs = st
        if canon_docs:
            msgs = [{**st[0], "content": docify_system_prompt(st[0]["content"])}] + st[1:]
        for _ in range(k):
            tasks.append(gen(client, sem, ep, msgs, pp))
    outs = await asyncio.gather(*tasks)
    for o in outs:
        if o is None:
            fail += 1
            continue
        total += 1
        f = classify(o)
        if f:
            per_sample_malformed += 1
            fams.update(f)
    rate = per_sample_malformed / max(total, 1)
    return total, fail, rate, dict(fams)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()

    r1_ep = os.environ["R1_EP"].rstrip("/")
    base_ep = os.environ.get("BASE_EP", "").rstrip("/")

    states = collect_states(args.n)
    print(f"states: {len(states)}  samples/state: {args.k}", flush=True)

    cells = [("pp1.5+pydocs", 1.5, False), ("pp0+pydocs", 0.0, False),
             ("pp1.5+canon", 1.5, True), ("pp0+canon", 0.0, True)]
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient() as client:
        print("\n== r1 checkpoint (defect carrier) ==", flush=True)
        for name, pp, canon in cells:
            total, fail, rate, fams = await run_cell(client, sem, r1_ep, states, pp, canon, args.k)
            print(f"  {name:<14} n={total} fail={fail}  malformed={rate:.1%}  {fams}", flush=True)
        if base_ep:
            print("\n== base-2B (seed-rate test) ==", flush=True)
            for name, pp, canon in cells:
                total, fail, rate, fams = await run_cell(client, sem, base_ep, states, pp, canon, args.k)
                print(f"  {name:<14} n={total} fail={fail}  malformed={rate:.1%}  {fams}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
