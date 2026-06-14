"""On-policy DAgger probe — where would the teacher push r10, on r10's OWN states?

The valid feasibility read for OPD (vs the retired off-policy log-prob probe):
roll out the student (r10 under the R11 scaffold) in real play, then on r10's own
states query the teacher (base+scaffold) for its action. r10's action is already in
the log — its real on-policy sample — so only the teacher is generated. This is
DAgger labeling (roll out the learner, ask the expert on the learner's states): on
the student's own distribution, by actual sampled behavior, no teacher-forced
log-prob and no thresholds.

Input: r10's eval rollout logs (default /tmp/kaetram_eval_r10-sft/logs from
scripts/run-eval.sh). Output: dataset/opd_probe/onpolicy_scores.jsonl + a summary
of teacher↔r10 disagreement and the verb-shift distribution.
"""
import sys, json, asyncio, argparse, random, os, contextlib
from collections import Counter
from pathlib import Path
import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "log_analysis"))
from opd_probe import (reconstruct_session, turn_to_openai_assistant, result_to_openai,
                       est_tokens, MAX_HIST_TURNS, TEACHER)

TOOL_DEFS = json.loads((REPO / "dataset/opd_probe/tool_defs.json").read_text())
GREEDY = {"temperature": 0.0, "top_p": 1.0, "top_k": 1, "presence_penalty": 0.0}
GEN_MAX_TOKENS = 600
GEN_STOP = ["</tool_call>"]
CREDIT = ("query_quest", "observe", "gather", "interact_npc")
DEFAULT_LOGS = "/tmp/kaetram_eval_r10-sft/logs"


@contextlib.contextmanager
def _quiet():
    with open(os.devnull, "w") as dn, contextlib.redirect_stdout(dn):
        yield


def _short(name):
    return name.split("__")[-1] if "__" in name else name


def collect_states(logs_dir, want, run=None):
    """Sample r10's own action-states from its rollout logs (seed 7).

    Two layouts: eval (`run_*/session_*.log` under logs_dir) and orchestrate
    (`agent_*/runs/<run>/session_*.log` under dataset/raw when --run is given)."""
    base = Path(logs_dir)
    logs = (sorted(base.glob(f"agent_*/runs/{run}/session_*.log")) if run
            else sorted(base.glob("run_*/session_*.log")))
    cands = []
    for log in logs:
        try:
            with _quiet():
                _bm, turns = reconstruct_session(log)
        except Exception:
            continue
        for turn, _results in turns:
            if turn.tool_calls:
                cands.append({"log_path": str(log),
                              "tuid": turn.tool_calls[0].get("id"),
                              "r10_verb": turn.short_tool_names[0]})
    random.seed(7)
    random.shuffle(cands)
    return cands[:want], len(cands)


async def teacher_action(client, sysmsg, boot, tail):
    """Greedy teacher (base+scaffold) action at this state. (short_tool, args)|None."""
    messages = [sysmsg, boot] + list(tail)
    body = {"messages": messages, "tools": TOOL_DEFS, "max_tokens": GEN_MAX_TOKENS,
            "stop": GEN_STOP, **GREEDY}
    for attempt in range(4):
        try:
            r = await client.post(f"{TEACHER}/chat/completions", json=body, timeout=240)
            if r.status_code == 200:
                break
        except (httpx.TimeoutException, httpx.HTTPError):
            pass
        await asyncio.sleep(2.0 * (attempt + 1))
    else:
        return None
    tcs = (r.json().get("choices") or [{}])[0].get("message", {}).get("tool_calls") or []
    return _short(tcs[0].get("function", {}).get("name", "")) if tcs else None


async def score_state(client, c, tail, sysmsg, boot, sem):
    if est_tokens([sysmsg, boot] + tail) > 26000:
        return None
    async with sem:
        teacher_verb = await teacher_action(client, sysmsg, boot, tail)
    return {
        "r10_verb": c["r10_verb"], "teacher_verb": teacher_verb,
        "agree": bool(teacher_verb and teacher_verb == c["r10_verb"]),
        "log": Path(c["log_path"]).name,
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=DEFAULT_LOGS, help="rollout logs dir (eval) or dataset/raw (orchestrate, with --run)")
    ap.add_argument("--run", default=None, help="orchestrate run id under dataset/raw/agent_*/runs/")
    ap.add_argument("--want", type=int, default=120)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default="dataset/opd_probe/onpolicy_scores.jsonl")
    args = ap.parse_args()

    sel, n_all = collect_states(args.logs, args.want, run=args.run)
    print(f"r10 on-policy states: {n_all} found -> sampled {len(sel)}", flush=True)
    if not sel:
        print(f"No states under {args.logs} — run scripts/run-eval.sh first.")
        return
    by_log = {}
    for c in sel:
        by_log.setdefault(c["log_path"], []).append(c)

    sem = asyncio.Semaphore(args.concurrency)
    out_fp = Path(args.out).open("w")
    n = 0
    timeout = httpx.Timeout(connect=60, read=240, write=60, pool=60)
    async with httpx.AsyncClient(timeout=timeout) as client:
        coros = []
        for log_path, want_pts in by_log.items():
            want = {c["tuid"]: c for c in want_pts}
            with _quiet():
                base_messages, turns = reconstruct_session(Path(log_path))
            if not turns:
                continue
            sysmsg, boot = base_messages[0], base_messages[1]
            rolling = list(base_messages)
            for turn, results in turns:
                tc = next((t for t in turn.tool_calls if t.get("id") in want), None)
                if tc is not None:
                    hist = rolling[2:]
                    tail = hist[-(2 * MAX_HIST_TURNS):] if len(hist) > 2 * MAX_HIST_TURNS else list(hist)
                    coros.append(score_state(client, want[tc["id"]], list(tail), sysmsg, boot, sem))
                rolling.append(turn_to_openai_assistant(turn))
                for tr in results:
                    rolling.append(result_to_openai(tr))
        print(f"built {len(coros)} state tasks; concurrency {args.concurrency}", flush=True)
        for fut in asyncio.as_completed([asyncio.create_task(c) for c in coros]):
            rec = await fut
            if rec is not None:
                out_fp.write(json.dumps(rec) + "\n"); out_fp.flush(); n += 1
            if n % 10 == 0 and n:
                print(f"  {n}/{len(coros)}", flush=True)
    out_fp.close()
    print(f"\nDONE: {n} states -> {args.out}\n")
    _summary(args.out)


def _summary(path):
    rows = [json.loads(l) for l in open(path)]
    parsed = [r for r in rows if r.get("teacher_verb")]
    print(f"## On-policy DAgger probe (teacher = base+R11 on r10's own states)")
    print(f"  states: {len(rows)}  teacher parseable: {len(parsed)}")
    if not parsed:
        return
    agree = sum(1 for r in parsed if r["agree"])
    print(f"  teacher == r10 (agreement): {agree}/{len(parsed)} ({100*agree/len(parsed):.0f}%)")
    print(f"  teacher != r10 (would change): {len(parsed)-agree}/{len(parsed)} "
          f"({100*(len(parsed)-agree)/len(parsed):.0f}%)")
    r10_mix = Counter(r["r10_verb"] for r in parsed)
    t_mix = Counter(r["teacher_verb"] for r in parsed)
    print("\n  tool mix (r10 actual on-policy  vs  teacher-preferred):")
    for v in sorted(set(r10_mix) | set(t_mix), key=lambda v: -(r10_mix[v] + t_mix[v])):
        tag = "  <- credit" if v in CREDIT else ""
        print(f"    {v:<14} r10 {r10_mix[v]:>3} ({100*r10_mix[v]/len(parsed):>3.0f}%)   "
              f"teacher {t_mix[v]:>3} ({100*t_mix[v]/len(parsed):>3.0f}%){tag}")
    print("\n  top verb-shifts on disagreement (r10 -> teacher):")
    shifts = Counter((r["r10_verb"], r["teacher_verb"]) for r in parsed if not r["agree"])
    for (a, b), n in shifts.most_common(10):
        tag = "  <- to credit" if b in CREDIT else ""
        print(f"    {a:>14} -> {b:<14} {n}{tag}")


if __name__ == "__main__":
    asyncio.run(main())
