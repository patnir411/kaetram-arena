"""Cook-state grading probe: can the 4B teacher GRADE a skill it never performs?

The Rick's-Roll null ("a teacher cannot grade what it cannot do") currently
rests on generative evidence — the 4B never emits craft_item in its own runs
and the r3 student showed zero cook transfer. Generative non-occurrence is not
grading incapacity: a model can put useful relative probability on a correct
action without ever sampling it. This probe measures the grading side directly.

Design: at REAL states from the r3 seeded collections where the student stood
mid-Rick's with raw shrimp in inventory (the cook-decision states), score four
candidate continuations under the 4B /score endpoint — the correct cook call
plus three distractors, all in the identical canonical wire wrapper — and take
the margin = mean-logprob(correct) − max(mean-logprob over distractors).
CONTROL: the same four-way comparison at Herbalist lily states (Foraging >= 5,
Blue Lily Bush nearby) where the teacher demonstrably IS competent (it passes
that wall in its own runs). A competent grader shows a positive margin at
control states; the support-hole hypothesis predicts the margin at cook states
is materially smaller or negative. The control anchors the contrast so a flat
cook margin reads as a coverage hole, not generic scorer flatness.

Contexts are reconstructed byte-parity with the OPD build (reconstruct_session
+ turn_to_chat + 28-message tail + patched chat template), so the teacher is
graded under exactly the distribution the training pipeline queries it on.

Usage:
  FOURB_EP=http://127.0.0.1:8104/v1 \
      python3 scripts/opd/cook_grade_probe.py -n 30

No endpoint is launched by this script. The zero-spend workflow uses an
already-running local scorer; do not point it at a metered service.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "opd"))
sys.path.insert(0, str(REPO / "finetune"))

from opd_probe import reconstruct_session  # noqa: E402
from opd_round1 import turn_to_chat  # noqa: E402
from render import patch_qwen_chat_template  # noqa: E402
from endpoint_policy import require_zero_spend_endpoints  # noqa: E402
MAX_HIST_MSGS = 28

# r3 seeded collections (milestone ladder incl. Rick's fishing/cook states).
SEEDED_RUNS = ["run_20260612_171400", "run_20260612_194443"]
# r2 unseeded eval — source of teacher-competent lily control states.
CONTROL_RUN = "run_20260612_044933"


def _call(fn: str, params: list[tuple[str, str]]) -> str:
    """Canonical wire form, identical wrapper for every candidate."""
    body = "".join(f"<parameter={k}>\n{v}\n</parameter>\n" for k, v in params)
    return (f"<tool_call>\n<function={fn}>\n{body}</function>\n</tool_call>"
            f"<|im_end|>\n")


CANDIDATES_COOK = {
    "cook (correct)": _call("craft_item", [("skill", "cooking"),
                                           ("recipe_key", "cookedshrimp"),
                                           ("count", "1")]),
    "gather-oak": _call("gather", [("resource_name", "Oak")]),
    "attack-rat": _call("attack", [("mob_name", "Rat")]),
    "observe": _call("observe", []),
}
CANDIDATES_CONTROL = {
    "gather-lily (correct)": _call("gather", [("resource_name", "Blue Lily Bush")]),
    "craft-cook": _call("craft_item", [("skill", "cooking"),
                                       ("recipe_key", "cookedshrimp"),
                                       ("count", "1")]),
    "attack-rat": _call("attack", [("mob_name", "Rat")]),
    "observe": _call("observe", []),
}


def _tail(msgs: list) -> list:
    head, hist = msgs[:2], msgs[2:]
    return head + (hist[-MAX_HIST_MSGS:] if len(hist) > MAX_HIST_MSGS else hist)


def _payload_has(result_str: str, needle: str) -> bool:
    return needle in (result_str or "")


def collect_states(run_ids: list[str], want: str, limit: int) -> list[dict]:
    """Rolling-context states whose latest observe satisfies the predicate.

    want='cook'    — inventory holds rawshrimp (the cook-decision state).
    want='control' — Blue Lily Bush visible AND foraging level >= 5.
    One state per session (the first qualifying decision turn) to keep states
    independent-ish; contexts are the byte-parity build rendering.
    """
    out = []
    for run_id in run_ids:
        logs = sorted((REPO / "dataset" / "raw").glob(
            f"agent_*/runs/{run_id}/session_*.log"))
        for lp in logs:
            try:
                base_messages, turns = reconstruct_session(lp)
            except Exception:
                continue
            rolling = list(base_messages)
            qualifying = None
            latest_obs = ""
            for turn, results in turns:
                # Decision turn AFTER a qualifying observe: probe the context
                # as the model saw it when choosing its next action.
                if qualifying is None and latest_obs:
                    if want == "cook" and _payload_has(latest_obs, "rawshrimp"):
                        qualifying = _tail(rolling)
                    elif want == "control" and "Blue Lily Bush" in latest_obs:
                        try:
                            payload = json.loads(
                                latest_obs.split("\n\nSTUCK_CHECK", 1)[0])
                            lvl = ((payload.get("skills") or {})
                                   .get("Foraging") or {}).get("level", 0)
                            if int(lvl or 0) >= 5:
                                qualifying = _tail(rolling)
                        except (json.JSONDecodeError, TypeError, ValueError):
                            pass
                rolling.append(turn_to_chat(turn))
                for tr in results:
                    rolling.append({"role": "tool", "content": tr.result_str,
                                    "name": tr.name})
                    if tr.name == "observe":
                        latest_obs = tr.result_str or ""
            if qualifying:
                out.append({"messages": qualifying, "session": lp.name})
            if len(out) >= limit:
                return out
    return out


async def _score_ep(client, sem, ep, ctx, full):
    async with sem:
        for attempt in range(4):
            try:
                r = await client.post(f"{ep}/score", json={
                    "context_text": ctx, "full_text": full}, timeout=300)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 400:
                    return None
            except (httpx.TimeoutException, httpx.HTTPError):
                pass
            await asyncio.sleep(3.0 * (attempt + 1))
    return None


async def probe(tok, states, candidates, label, teacher_ep, student_ep=None):
    """Per state, score every candidate on the teacher (and student if
    student_ep is set). Margins are computed on the quantity that actually
    drives training when both endpoints are available: the mean per-token
    ADVANTAGE (teacher − student logprob); otherwise on raw teacher logprob."""
    signal = "advantage (teacher−student)" if student_ep else "teacher logprob"
    print(f"\n--- {label}: {len(states)} states x {len(candidates)} candidates "
          f"[signal: {signal}] ---")
    margins = []
    per_cand = {c: [] for c in candidates}
    per_cand_t = {c: [] for c in candidates}
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient() as client:
        for st in states:
            ctx = tok.apply_chat_template(
                st["messages"], tokenize=False, add_generation_prompt=True)
            t_resps = await asyncio.gather(*[
                _score_ep(client, sem, teacher_ep, ctx, ctx + em)
                for em in candidates.values()])
            s_resps = [None] * len(candidates)
            if student_ep:
                s_resps = await asyncio.gather(*[
                    _score_ep(client, sem, student_ep, ctx, ctx + em)
                    for em in candidates.values()])
            means, t_means = {}, {}
            for cand, t_resp, s_resp in zip(candidates, t_resps, s_resps):
                if t_resp is None or (student_ep and s_resp is None):
                    break
                t_vals = t_resp["target_logprobs"]
                if student_ep:
                    s_vals = s_resp["target_logprobs"]
                    if t_resp["target_token_ids"] != s_resp["target_token_ids"]:
                        break  # tokenization boundary guard
                    pairs = [(t, s) for t, s in zip(t_vals, s_vals)
                             if t is not None and s is not None]
                    if not pairs:
                        break
                    means[cand] = statistics.fmean(t - s for t, s in pairs)
                    t_means[cand] = statistics.fmean(t for t, _ in pairs)
                else:
                    vals = [v for v in t_vals if v is not None]
                    if not vals:
                        break
                    means[cand] = statistics.fmean(vals)
                    t_means[cand] = means[cand]
            if len(means) != len(candidates):
                continue
            correct = next(c for c in candidates if "(correct)" in c)
            margin = means[correct] - max(v for c, v in means.items() if c != correct)
            margins.append(margin)
            for c in candidates:
                per_cand[c].append(means[c])
                per_cand_t[c].append(t_means[c])
            print(f"  {st['session']:<42} margin={margin:+.3f}  "
                  + "  ".join(f"{c}={means[c]:+.3f}" for c in candidates))
    if margins:
        print(f"  => median margin on {signal} (correct − best distractor): "
              f"{statistics.median(margins):+.3f} nats over {len(margins)} states; "
              f"positive fraction {sum(m > 0 for m in margins)}/{len(margins)}")
        for c, vals in per_cand.items():
            extra = (f"   (teacher-only {statistics.fmean(per_cand_t[c]):+.3f})"
                     if student_ep else "")
            print(f"     mean {signal} {c:<22} {statistics.fmean(vals):+.3f}{extra}")
    return margins


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=30, help="states per condition")
    ap.add_argument(
        "--allow-metered-remote-endpoints",
        action="store_true",
        help="explicitly authorize non-loopback endpoints that may incur charges",
    )
    args = ap.parse_args()
    teacher_ep = os.environ["FOURB_EP"].rstrip("/")
    student_ep = (os.environ.get("STUDENT_EP") or "").rstrip("/") or None
    checked = require_zero_spend_endpoints(
        [teacher_ep] + ([student_ep] if student_ep else []),
        allow_metered_remote_endpoints=args.allow_metered_remote_endpoints,
    )
    teacher_ep = checked[0]
    student_ep = checked[1] if student_ep else None

    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-2B", trust_remote_code=True)
    patch_qwen_chat_template(tok)

    cook_states = collect_states(SEEDED_RUNS, "cook", args.n)
    ctrl_states = collect_states([CONTROL_RUN], "control", args.n)
    print(f"collected: {len(cook_states)} cook states, {len(ctrl_states)} control states")
    if not cook_states or not ctrl_states:
        print("insufficient states — check run IDs / predicates")
        sys.exit(1)

    cook_m = await probe(
        tok, cook_states, CANDIDATES_COOK, "COOK (support-hole hypothesis)",
        teacher_ep, student_ep,
    )
    ctrl_m = await probe(
        tok, ctrl_states, CANDIDATES_CONTROL, "CONTROL (teacher-competent lily)",
        teacher_ep, student_ep,
    )

    if cook_m and ctrl_m:
        dm = statistics.median(ctrl_m) - statistics.median(cook_m)
        print(f"\n=== VERDICT: control-minus-cook median-margin gap = {dm:+.3f} nats ===")
        print("(support hole ⇔ control margin clearly positive while cook margin ~0/negative;")
        print(" a clearly positive cook margin instead FALSIFIES the 'cannot grade' claim)")


if __name__ == "__main__":
    asyncio.run(main())
