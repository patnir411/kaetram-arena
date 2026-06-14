"""r11 OPD round-1 data-build (Modal) + pre-train reverse-KL diagnostic.

Reuses the June-5 r10+R11 rollouts as r10's own on-policy data. For each assistant
action turn it scores the SAME (context, action) on both Modal `/v1/score` endpoints —
student r10 (behavior logprobs) and teacher base+scaffold — and emits a pre-tokenized
OPD training record:

    input_ids          = context_ids + target_ids
    labels             = -100 over context, target token ids over the action
    advantages         = 0 over context, -coef*(logp_student - logp_teacher) over action
    behavior_logprobs  = 0 over context, student logprobs over the action

Alignment is guaranteed by construction: input_ids = (our render of messages[:-1] with
add_generation_prompt) + the endpoint's target_token_ids, and we assert our context length
== the endpoint's n_context_tokens. The same loop accumulates a per-verb x Core-3-frontier
mean reverse-KL — the free pre-train signal gate (does base diverge from r10 on the
regressed verbs?). No training here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(HERE), str(REPO), str(REPO / "scripts" / "log_analysis"), str(REPO / "finetune")):
    if p not in sys.path:
        sys.path.insert(0, p)

from opd_probe import reconstruct_session, STUDENT, TEACHER  # noqa: E402
from opd_round1 import load_tokenizer, turn_to_chat  # noqa: E402
from opd_wall_probe import _frontier, _finished_from_payload  # noqa: E402

R10_RUN_IDS = ["run_20260605_173451", "run_20260605_223917"]
MAX_HIST_MSGS = 28          # system+bootstrap + last N history msgs (bounds context)
MAX_SEQ = 16384             # match train_modal max_seq_length; drop overlong turns
KL_COEF = 1.0


def collect_action_states(run_ids):
    """Yield per-turn (messages, base_verb, frontier, session) over the r10 logs.

    messages = [system, bootstrap, ...tail history..., assistant(action)] — the exact
    list scored on both endpoints and used to build the training record."""
    logs = []
    for run in run_ids:
        logs.extend(sorted((REPO / "dataset" / "raw").glob(f"agent_*/runs/{run}/session_*.log")))
    states = []
    for lp in logs:
        try:
            base_messages, turns = reconstruct_session(lp)
        except Exception:
            continue
        if not turns:
            continue
        rolling = list(base_messages)
        finished: set[str] = set()
        for turn, results in turns:
            if turn.tool_calls:
                hist = rolling[2:]
                tail = hist[-MAX_HIST_MSGS:] if len(hist) > MAX_HIST_MSGS else hist
                msgs = rolling[:2] + list(tail) + [turn_to_chat(turn)]
                states.append({
                    "messages": msgs,
                    "verb": turn.short_tool_names[0],
                    "frontier": _frontier(finished),
                    "session": lp.name,
                })
            rolling.append(turn_to_chat(turn))
            for tr in results:
                rolling.append({"role": "tool", "content": tr.result_str, "name": tr.name})
                fin = _finished_from_payload(tr.payload)
                if fin is not None:
                    finished = fin
    return states


async def _score(client, endpoint, messages):
    body = {"messages": messages}
    for attempt in range(4):
        try:
            r = await client.post(f"{endpoint}/score", json=body, timeout=240)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 400:
                return None  # malformed turn (e.g. target empty) — skip
        except (httpx.TimeoutException, httpx.HTTPError):
            pass
        await asyncio.sleep(2.0 * (attempt + 1))
    return None


async def build_record(client, tok, st, sem):
    msgs = st["messages"]
    ctx_text = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
    ctx_ids = tok.encode(ctx_text, add_special_tokens=False)
    async with sem:
        s_resp, t_resp = await asyncio.gather(
            _score(client, STUDENT, msgs), _score(client, TEACHER, msgs))
    if not s_resp or not t_resp:
        return None, "score_fail"
    target = s_resp["target_token_ids"]
    if target != t_resp["target_token_ids"]:
        return None, "target_mismatch"
    if len(ctx_ids) != s_resp["n_context_tokens"]:
        return None, "ctx_len_mismatch"
    if len(ctx_ids) + len(target) > MAX_SEQ:
        return None, "overlong"
    s_lp = s_resp["target_logprobs"]
    t_lp = t_resp["target_logprobs"]
    adv_t, beh_t, rkls = [], [], []
    for si, ti in zip(s_lp, t_lp):
        if si is None or ti is None:
            adv_t.append(0.0); beh_t.append(0.0)
        else:
            rkl = si - ti
            adv_t.append(-KL_COEF * rkl); beh_t.append(si); rkls.append(rkl)
    rec = {
        "input_ids": ctx_ids + target,
        "labels": [-100] * len(ctx_ids) + list(target),
        "advantages": [0.0] * len(ctx_ids) + adv_t,
        "behavior_logprobs": [0.0] * len(ctx_ids) + beh_t,
        "verb": st["verb"], "frontier": st["frontier"], "session": st["session"],
        "n_action": len(target), "mean_rkl": (sum(rkls) / len(rkls)) if rkls else 0.0,
    }
    return rec, "ok"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", nargs="+", default=R10_RUN_IDS)
    ap.add_argument("--max-sessions", type=int, default=0, help="0 = all")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--out", default="dataset/opd_r11/round1/records.jsonl")
    args = ap.parse_args()

    tok = load_tokenizer()
    states = collect_action_states(args.run_ids)
    if args.max_sessions:
        keep = set(sorted({s["session"] for s in states})[:args.max_sessions])
        states = [s for s in states if s["session"] in keep]
    print(f"action-states: {len(states)} (endpoints: student=r10, teacher=base)", flush=True)

    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.concurrency)
    reasons = defaultdict(int)
    diag = defaultdict(lambda: [0.0, 0])  # (verb,frontier) -> [sum_rkl, n_tokens]
    n_ok = 0
    timeout = httpx.Timeout(connect=60, read=240, write=60, pool=60)
    with out.open("w") as fp:
        async with httpx.AsyncClient(timeout=timeout) as client:
            tasks = [asyncio.create_task(build_record(client, tok, st, sem)) for st in states]
            for i, fut in enumerate(asyncio.as_completed(tasks)):
                rec, why = await fut
                reasons[why] += 1
                if rec is None:
                    continue
                fp.write(json.dumps({k: rec[k] for k in
                         ("input_ids", "labels", "advantages", "behavior_logprobs",
                          "verb", "frontier")}) + "\n")
                n_ok += 1
                # diagnostic: per-token reverse-KL bucketed by verb x frontier
                key = (rec["verb"], rec["frontier"])
                # mean_rkl is per-action-token mean; weight by n_action
                diag[key][0] += rec["mean_rkl"] * rec["n_action"]
                diag[key][1] += rec["n_action"]
                if n_ok % 100 == 0:
                    print(f"  {n_ok} records written ({i+1}/{len(states)} processed)", flush=True)
    print(f"\nwrote {n_ok} records -> {args.out}")
    print(f"skip/fail reasons: {dict(reasons)}")
    _print_diagnostic(diag)


def _print_diagnostic(diag):
    print("\n## Pre-train reverse-KL diagnostic  (mean logp_student - logp_teacher, per action token)")
    print("   positive => r10 over-confident vs base (OPD will suppress); the GATE is whether KL")
    print("   concentrates on the regressed verbs (interact_npc/query_quest) vs flat everywhere.\n")
    # by verb (aggregate over frontiers)
    by_verb = defaultdict(lambda: [0.0, 0])
    by_front = defaultdict(lambda: [0.0, 0])
    for (verb, front), (s, n) in diag.items():
        by_verb[verb][0] += s; by_verb[verb][1] += n
        by_front[front][0] += s; by_front[front][1] += n
    print("  by tool verb:")
    for verb in sorted(by_verb, key=lambda v: -(by_verb[v][0] / max(by_verb[v][1], 1))):
        s, n = by_verb[verb]
        if n:
            print(f"    {verb:<14} mean_rkl {s/n:+.4f}   ({n} action tokens)")
    print("\n  by Core-3 frontier:")
    for front in ["Foresting", "Herbalist", "Ricks", "post-core"]:
        s, n = by_front.get(front, [0.0, 0])
        if n:
            print(f"    {front:<12} mean_rkl {s/n:+.4f}   ({n} action tokens)")


if __name__ == "__main__":
    asyncio.run(main())
