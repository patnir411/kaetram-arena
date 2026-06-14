"""4B-vs-9B OPD-teacher diagnostic, scored on the base-2B student's own rollouts.

No training. For a sample of the base-2B agent's action turns it scores the SAME
(context, action) on three /v1/score endpoints — the 2B student, the 4B teacher,
and the 9B teacher — and compares, per action token:

  teacher support   = mean logp_teacher on the 2B's chosen tokens (higher = the
                      teacher assigns more mass to what the 2B actually does =
                      more learnable, lower-noise reverse-KL signal)
  reverse-KL        = logp_student - logp_teacher (the OPD per-token signal)

aggregated overall, by context-length tercile (the trajectory-depth axis — longer
context = deeper into the multi-turn cascade, where a small agent drifts off the
teacher's support), and by tool verb. The teacher with higher support and a
shallower support-decay across depth is the safer, more-aligned supervisor.

Usage:
  TWOB_EP=https://.../v1 FOURB_EP=https://.../v1 NINEB_EP=https://.../v1 \
    python3 scripts/opd/teacher_diag.py --run-ids run_20260608_185339 -n 250
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "opd"))

from opd_probe import reconstruct_session  # noqa: E402
from opd_round1 import turn_to_chat  # noqa: E402

FOURB_EP = os.environ["FOURB_EP"].rstrip("/")
NINEB_EP = os.environ["NINEB_EP"].rstrip("/")
MAX_HIST_MSGS = 30
MAX_SEQ = 16384


def collect_states(run_ids, stride):
    """Action turns with a turn-depth index, sampled by `stride` across the run."""
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
        for idx, (turn, results) in enumerate(turns):
            if turn.tool_calls:
                hist = rolling[2:]
                tail = hist[-MAX_HIST_MSGS:] if len(hist) > MAX_HIST_MSGS else hist
                msgs = rolling[:2] + list(tail) + [turn_to_chat(turn)]
                states.append({"messages": msgs, "verb": turn.short_tool_names[0], "turn_idx": idx})
            rolling.append(turn_to_chat(turn))
            for tr in results:
                rolling.append({"role": "tool", "content": tr.result_str, "name": tr.name})
    return states[::stride]


async def _score(client, endpoint, messages):
    for attempt in range(5):
        try:
            r = await client.post(f"{endpoint}/score", json={"messages": messages}, timeout=300)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 400:
                return None
        except (httpx.TimeoutException, httpx.HTTPError):
            pass
        await asyncio.sleep(3.0 * (attempt + 1))
    return None


def _mean_lp(resp):
    lps = [x for x in resp["target_logprobs"] if x is not None]
    return (statistics.fmean(lps) if lps else None), len(lps)


async def score_state(client, st, sem):
    # Score the 2B's fixed action tokens on the two TEACHERS. The student endpoint
    # is not needed: the reverse-KL ranking between teachers depends only on the
    # teacher logprobs (the common logp_student term cancels), and the 4B/9B share
    # a tokenizer so their targets align (the 2B renders the turn differently).
    async with sem:
        a, b = await asyncio.gather(
            _score(client, FOURB_EP, st["messages"]),
            _score(client, NINEB_EP, st["messages"]),
        )
    if not (a and b):
        return None
    if a["target_token_ids"] != b["target_token_ids"]:
        return None
    if a["n_context_tokens"] + a["n_target_tokens"] > MAX_SEQ:
        return None
    lp4, n = _mean_lp(a)
    lp9, _ = _mean_lp(b)
    if None in (lp4, lp9) or n == 0:
        return None
    return {
        "verb": st["verb"], "turn_idx": st["turn_idx"], "n_ctx": a["n_context_tokens"],
        "lp4": lp4, "lp9": lp9, "ntok": n,
    }


def _band(rows, key):
    if not rows:
        return "  (none)"
    s4 = statistics.fmean(r["lp4"] for r in rows)
    s9 = statistics.fmean(r["lp9"] for r in rows)
    # support = mean logp the teacher assigns to the 2B's own action tokens.
    # Δ = s4 - s9 > 0 means the 4B better covers what the 2B actually does
    # (more shared high-prob mass = more learnable, less off-support OPD noise).
    better = "4B" if s4 > s9 else "9B"
    return (f"  {key:<18} n={len(rows):<4} "
            f"support[4B]={s4:+.3f}  support[9B]={s9:+.3f}  "
            f"Δ(4B-9B)={s4-s9:+.3f}  better={better}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", nargs="+", required=True)
    ap.add_argument("-n", type=int, default=250)
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()

    all_states = collect_states(args.run_ids, stride=1)
    stride = max(1, len(all_states) // args.n)
    states = all_states[::stride][: args.n]
    print(f"collected {len(all_states)} action states; scoring {len(states)} "
          f"(stride {stride}) on 4B + 9B teachers...", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient() as client:
        # warm the three endpoints (scale-from-zero) before the batch
        if states:
            await asyncio.gather(
                _score(client, FOURB_EP, states[0]["messages"]),
                _score(client, NINEB_EP, states[0]["messages"]),
            )
        rows = [r for r in await asyncio.gather(*(score_state(client, st, sem) for st in states)) if r]

    if not rows:
        print("no scored states (endpoints down?)")
        return
    tok_total = sum(r["ntok"] for r in rows)
    print(f"\nscored {len(rows)} states, {tok_total} action tokens\n")
    print("support = mean logp the model assigns to the 2B's own action tokens (higher = more aligned)")
    print("rKL     = logp_2B - logp_teacher (the OPD signal; nearer 0 = teacher already agrees)\n")

    print("OVERALL")
    print(_band(rows, "all"))

    # depth axis: context-length terciles (longer ctx = deeper into the cascade)
    ctxs = sorted(r["n_ctx"] for r in rows)
    q1, q2 = ctxs[len(ctxs) // 3], ctxs[2 * len(ctxs) // 3]
    print("\nBY TRAJECTORY DEPTH (context-length tercile; does teacher support decay deeper in?)")
    print(_band([r for r in rows if r["n_ctx"] <= q1], f"short(<= {q1})"))
    print(_band([r for r in rows if q1 < r["n_ctx"] <= q2], f"mid"))
    print(_band([r for r in rows if r["n_ctx"] > q2], f"long(> {q2})"))

    print("\nBY TOOL VERB (top by frequency)")
    byv = defaultdict(list)
    for r in rows:
        byv[r["verb"]].append(r)
    for verb in sorted(byv, key=lambda v: -len(byv[v]))[:10]:
        print(_band(byv[verb], verb))


if __name__ == "__main__":
    asyncio.run(main())
