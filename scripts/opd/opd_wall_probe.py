"""Wall-divergence probe — does the 27B teacher diverge from base-9B WHERE IT MATTERS?

Round 1's aggregate reverse-KL was small (27B ≈ base-9B on base's own tokens). That
average is diluted by easy tokens; the question is whether the teacher acts differently
at the Core-3 frontier where base-9B+scaffold plateaus (Rick's Roll). This walks base-9B's
own rollout states, tags each by Core-3 frontier (from the latest observe), samples the
27B teacher's action via Tinker on that exact state, and reports teacher↔base agreement +
verb-shifts, sliced by frontier. Offline (reuses reconstruction), no game env.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(HERE), str(REPO), str(REPO / "scripts" / "log_analysis"), str(REPO / "finetune")):
    if p not in sys.path:
        sys.path.insert(0, p)

from opd_probe import reconstruct_session  # noqa: E402
from opd_round1 import load_tokenizer, turn_to_chat, RUN_IDS  # noqa: E402

TEACHER_MODEL = "Qwen/Qwen3.5-27B"
MAX_HIST_MSGS = 28  # system+bootstrap + last N history messages (bounds context length)
_FUNC_RE = re.compile(r"<function=(\w+)")
CORE3 = ["Foresting", "Herbalist's Desperation", "Rick's Roll"]


def _frontier(finished: set[str]) -> str:
    for q in CORE3:
        if q not in finished:
            return {"Foresting": "Foresting", "Herbalist's Desperation": "Herbalist",
                    "Rick's Roll": "Ricks"}[q]
    return "post-core"


def _finished_from_payload(payload) -> set[str] | None:
    """finished_quests from the already-decoded tool payload (handles both the
    full ASCII_MAP and compact STUCK_CHECK observe shapes via parse.py's decoder)."""
    if not isinstance(payload, dict):
        return None
    fq = payload.get("finished_quests")
    if fq is None:
        return None
    return {q.get("name") for q in fq if isinstance(q, dict)}


def collect_states(run_ids, want, seed):
    """Return [(prompt_messages, base_verb, frontier, session)] across base-9B logs."""
    logs = []
    for run in run_ids:
        logs.extend(sorted((REPO / "dataset" / "raw").glob(f"agent_*/runs/{run}/session_*.log")))
    random.seed(seed)
    random.shuffle(logs)
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
                tail = hist[-(MAX_HIST_MSGS):] if len(hist) > MAX_HIST_MSGS else hist
                prompt_msgs = rolling[:2] + list(tail)
                states.append({
                    "prompt": prompt_msgs,
                    "base_verb": turn.short_tool_names[0],
                    "frontier": _frontier(finished),
                    "session": lp.name,
                })
            rolling.append(turn_to_chat(turn))
            for tr in results:
                rolling.append({"role": "tool", "content": tr.result_str, "name": tr.name})
                fin = _finished_from_payload(tr.payload)
                if fin is not None:
                    finished = fin
    random.seed(seed + 1)
    random.shuffle(states)
    return states


def _extract_tokens(resp):
    """Pull generated token ids out of a SampleResponse across shapes."""
    for attr in ("sequences", "samples", "sampled_sequences"):
        seqs = getattr(resp, attr, None)
        if seqs:
            s0 = seqs[0]
            for t in ("tokens", "token_ids", "output_tokens"):
                v = getattr(s0, t, None)
                if v:
                    return list(v)
    try:
        d = resp.model_dump()
        seqs = d.get("sequences") or d.get("samples") or []
        if seqs:
            s0 = seqs[0]
            return s0.get("tokens") or s0.get("token_ids") or []
    except Exception:
        pass
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", nargs="+", default=RUN_IDS)
    ap.add_argument("--want", type=int, default=60)
    ap.add_argument("--max-ctx", type=int, default=20000)
    ap.add_argument("--focus", default="Ricks", help="prioritize this frontier")
    ap.add_argument("--out", default="dataset/opd_probe/wall_27b_scores.jsonl")
    args = ap.parse_args()

    import tinker
    tok = load_tokenizer()
    states = collect_states(args.run_ids, args.want, seed=7)
    by_f = Counter(s["frontier"] for s in states)
    print(f"collected {len(states)} action-states; by frontier: {dict(by_f)}")

    # prioritize the focus frontier, then fill with the rest
    focus = [s for s in states if s["frontier"] == args.focus]
    rest = [s for s in states if s["frontier"] != args.focus]
    sel = (focus + rest)[:args.want]
    print(f"probing {len(sel)} states (focus='{args.focus}': {sum(1 for s in sel if s['frontier']==args.focus)})")

    sc = tinker.ServiceClient()
    teacher = sc.create_sampling_client(base_model=TEACHER_MODEL)
    params = tinker.SamplingParams(max_tokens=600, temperature=0.0, top_k=1, stop=["</tool_call>"])

    out_fp = Path(args.out)
    out_fp.parent.mkdir(parents=True, exist_ok=True)
    fp = out_fp.open("w")
    rows = []
    dbg_shape = True
    for i, s in enumerate(sel):
        text = tok.apply_chat_template(s["prompt"], tokenize=False, add_generation_prompt=True)
        ids = tok.encode(text, add_special_tokens=False)
        if len(ids) > args.max_ctx:
            continue
        resp = teacher.sample(tinker.ModelInput.from_ints(ids), num_samples=1,
                              sampling_params=params).result()
        gen = _extract_tokens(resp)
        if dbg_shape:
            print(f"  [debug] sample resp type={type(resp).__name__} gen_tokens={len(gen)}")
            dbg_shape = False
        gen_text = tok.decode(gen) if gen else ""
        m = _FUNC_RE.search(gen_text)
        teacher_verb = m.group(1) if m else None
        rec = {"base_verb": s["base_verb"], "teacher_verb": teacher_verb,
               "agree": bool(teacher_verb and teacher_verb == s["base_verb"]),
               "frontier": s["frontier"], "session": s["session"]}
        rows.append(rec)
        fp.write(json.dumps(rec) + "\n"); fp.flush()
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(sel)}")
    fp.close()
    _summary(rows)


def _summary(rows):
    parsed = [r for r in rows if r.get("teacher_verb")]
    print(f"\n## Wall-divergence probe (teacher=27B on base-9B's own states)")
    print(f"  states: {len(rows)}  teacher parseable: {len(parsed)}")
    if not parsed:
        return
    for scope in ["ALL"] + sorted({r["frontier"] for r in parsed}):
        sub = parsed if scope == "ALL" else [r for r in parsed if r["frontier"] == scope]
        if not sub:
            continue
        agree = sum(1 for r in sub if r["agree"])
        print(f"\n  [{scope}] n={len(sub)}  teacher==base {agree} ({100*agree/len(sub):.0f}%)  "
              f"diverge {len(sub)-agree} ({100*(len(sub)-agree)/len(sub):.0f}%)")
        bmix = Counter(r["base_verb"] for r in sub)
        tmix = Counter(r["teacher_verb"] for r in sub)
        verbs = sorted(set(bmix) | set(tmix), key=lambda v: -(bmix[v] + tmix[v]))
        for v in verbs[:8]:
            print(f"     {v:<14} base {bmix[v]:>3}   teacher {tmix[v]:>3}")
        shifts = Counter((r["base_verb"], r["teacher_verb"]) for r in sub if not r["agree"])
        if shifts:
            print(f"     top shifts (base -> 27B):")
            for (a, b), n in shifts.most_common(6):
                print(f"        {a:>14} -> {b:<14} {n}")


if __name__ == "__main__":
    main()
