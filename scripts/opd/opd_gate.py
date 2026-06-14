"""Post-train behavioral gate: did this OPD round move the student safely?

Scores the NEW student endpoint on the held-out (context, action) pairs that
opd_2b_data.py set aside (session-level split, never trained). The teacher and
prior-student logprobs were captured at build time, so only one endpoint is
scored here.

Verdict (round-2 redesign — the old 30% per-token-rKL PASS bar is dropped;
KAT, arXiv:2606.09471, shows per-token KL on teacher-agreement-trapped spans
is a misleading quality signal):
  HARD FAIL on any of:
    (a) malformed tool-call rate in sampled completions WORSE than the r1
        reference (r1 rollouts: ~90% of quest-accept calls malformed) —
        the rKL objective is structurally blind to format defects;
    (b) degenerate completions (repetition collapse, Stable-OPD failure mode);
    (c) catastrophic rKL blow-up (new student moved AWAY from the teacher,
        token-weighted rKL > 2x prior student's).
  The held-out rKL delta (computed EXCLUDING masked malformed spans — the
  teacher's grading there is probe-verified unreliable) is reported as a
  DIRECTIONAL diagnostic only, per-verb.

Usage:
  NEW_STUDENT_EP=https://...modal.run/v1 \
    python3 scripts/opd/opd_gate.py --heldout dataset/opd_2b/round2/heldout.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "finetune"))

NEW_EP = os.environ["NEW_STUDENT_EP"].rstrip("/")

# Malformed parameter key (kwarg in the key): <parameter=accept_quest_offer=True>
MALFORMED_PARAM_RE = re.compile(r"<parameter=[^>\n]*=[^>\n]*>")
# r1 reference malformed rate among quest-accept-shaped calls (154/172 in the
# r1 eval rollouts). PASS = sampled rate not worse than this.
R1_MALFORMED_REF = 0.90

STUDENT_TOKENIZER_ID = "Qwen/Qwen3.5-2B"


def _load_tokenizer():
    from transformers import AutoTokenizer
    from render import patch_qwen_chat_template
    tok = AutoTokenizer.from_pretrained(STUDENT_TOKENIZER_ID, trust_remote_code=True)
    patch_qwen_chat_template(tok)
    return tok


def _masked_positions(tok, ctx_text, full_text):
    """Target-token indices overlapping malformed-param spans (to exclude)."""
    spans = [(m.start(), m.end()) for m in MALFORMED_PARAM_RE.finditer(full_text)
             if m.start() >= len(ctx_text)]
    if not spans:
        return set()
    enc = tok(full_text, add_special_tokens=False, return_offsets_mapping=True)
    n_ctx = len(tok.encode(ctx_text, add_special_tokens=False))
    offs = enc["offset_mapping"][n_ctx:]
    return {i for i, (a, b) in enumerate(offs)
            if any(a < e and b > s for s, e in spans)}


async def _score_raw(client, ctx_text, full_text, sem):
    body = {"context_text": ctx_text, "full_text": full_text}
    async with sem:
        for attempt in range(5):
            try:
                r = await client.post(f"{NEW_EP}/score", json=body, timeout=300)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 400:
                    return None
            except (httpx.TimeoutException, httpx.HTTPError):
                pass
            await asyncio.sleep(2.0 * (attempt + 1))
    return None


def _rkl(student_lp, teacher_lp, exclude=frozenset()):
    """(token-weighted sum, n) of |logp_student - logp_teacher| over scorable
    action tokens, skipping excluded (masked) positions."""
    vals = [abs(s - t) for i, (s, t) in enumerate(zip(student_lp, teacher_lp))
            if s is not None and t is not None and i not in exclude]
    return (statistics.fmean(vals), len(vals)) if vals else (None, 0)


SPOT_PROMPTS = [
    "You are a Kaetram game agent. You see a rat nearby and your HP is full. What do you do?",
    "Your inventory has 5 oak logs and the Forester asked for 10. What next?",
    "You just killed a snek. Describe your next action in one sentence.",
    "You are a Kaetram game agent. The Forester NPC is offering you the Foresting "
    "quest. Accept it using your interact_npc tool.",
    "You are a Kaetram game agent standing next to Herby Mc. Herb, who has a quest "
    "to offer. Emit the tool call that talks to him and accepts the quest offer.",
    "You are a Kaetram game agent. Rick is offering Rick's Roll. Emit the exact "
    "interact_npc tool call, accepting the offer.",
    "You are a Kaetram game agent at full HP with a Batterfly nearby. Emit your "
    "next tool call.",
    "You are a Kaetram game agent who needs to gather 3 bluelily from a Blue Lily "
    "Bush right next to you. Emit the tool call.",
    "Your HP is 20/300 and you have bread in slot 7. Emit the tool call.",
    "You finished collecting 10 logs for the Forester. Emit the tool call to turn "
    "in the quest.",
]


async def completion_checks(client):
    """Sampled-generation checks: repetition degeneration + malformed param keys.
    Accept-shaped prompts give the format check coverage on the defect family."""
    degenerate = 0
    malformed = 0
    with_param = 0
    for p in SPOT_PROMPTS:
        try:
            r = await client.post(f"{NEW_EP}/chat/completions", json={
                "model": "opd-gate",
                "messages": [{"role": "user", "content": p}],
                "max_tokens": 200,
            }, timeout=300)
            j = r.json()["choices"][0]
            text = (j["message"].get("content") or "")
            # Re-serialize any structured tool_calls the server parsed out so the
            # format regex sees the wire form too.
            for tc in (j["message"].get("tool_calls") or []):
                fn = tc.get("function", {})
                args = fn.get("arguments")
                if isinstance(args, str):
                    text += f" args={args}"
                elif isinstance(args, dict):
                    text += "".join(f"<parameter={k}>" for k in args)
        except Exception as e:
            print(f"  spot-check error: {type(e).__name__}: {e}")
            degenerate += 1
            continue
        words = text.split()
        grams = defaultdict(int)
        for i in range(len(words) - 2):
            grams[tuple(words[i:i + 3])] += 1
        rep = max(grams.values(), default=0)
        char_loop = bool(re.search(r"(.{3,30}?)\1{4,}", text))
        is_degen = rep >= 5 or char_loop
        n_mal = len(MALFORMED_PARAM_RE.findall(text))
        has_param = "<parameter=" in text
        if is_degen:
            degenerate += 1
        if has_param:
            with_param += 1
            if n_mal:
                malformed += 1
        status = "DEGEN" if is_degen else ("MALFORMED" if n_mal else "ok")
        print(f"  spot[{status}] rep3max={rep} mal={n_mal}  {text[:80]!r}")
    return degenerate, malformed, with_param


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--heldout", default="dataset/opd_2b/round2/heldout.jsonl")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("-n", type=int, default=0, help="cap heldout states (0 = all)")
    args = ap.parse_args()

    tok = _load_tokenizer()
    rows = []
    with open(REPO / args.heldout) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if args.n:
        rows = rows[: args.n]
    print(f"heldout states: {len(rows)}  (new student: {NEW_EP})", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient() as client:
        await _score_raw(client, rows[0]["context_text"], rows[0]["full_text"], sem)  # warm
        resps = await asyncio.gather(*(
            _score_raw(client, r["context_text"], r["full_text"], sem) for r in rows))

        base_vals, new_vals = [], []
        by_verb = defaultdict(lambda: [[], []])
        skipped = n_excluded = 0
        for row, resp in zip(rows, resps):
            if not resp:
                skipped += 1
                continue
            t_lp = row["teacher_logprobs"]
            if len(resp["target_logprobs"]) != len(t_lp):
                skipped += 1
                continue
            excl = _masked_positions(tok, row["context_text"], row["full_text"])
            n_excluded += len(excl)
            b, nb = _rkl(row["student_base_logprobs"], t_lp, excl)
            n, nn = _rkl(resp["target_logprobs"], t_lp, excl)
            if b is None or n is None:
                skipped += 1
                continue
            base_vals.append((b, nb))
            new_vals.append((n, nn))
            by_verb[row["verb"]][0].append(b)
            by_verb[row["verb"]][1].append(n)

        print(f"scored {len(base_vals)} states ({skipped} skipped, "
              f"{n_excluded} masked-span tokens excluded)\n")
        wb = sum(v * n for v, n in base_vals) / max(sum(n for _, n in base_vals), 1)
        wn = sum(v * n for v, n in new_vals) / max(sum(n for _, n in new_vals), 1)
        reduction = (wb - wn) / wb if wb else 0.0
        print("mean |rKL| to 4B teacher (token-weighted, held-out, masked spans excluded):")
        print(f"  prior student : {wb:.4f}")
        print(f"  new student   : {wn:.4f}")
        print(f"  delta         : {reduction:+.1%}   (DIRECTIONAL diagnostic — no PASS bar)\n")

        print("by verb (mean per-state rKL, prior -> new):")
        for verb in sorted(by_verb, key=lambda v: -len(by_verb[v][0]))[:12]:
            b, n = by_verb[verb]
            if b and n:
                print(f"  {verb:<14} {statistics.fmean(b):.4f} -> {statistics.fmean(n):.4f}   (n={len(b)})")

        print("\ncompletion checks (degeneration + format):")
        degenerate, malformed, with_param = await completion_checks(client)

    mal_rate = malformed / with_param if with_param else 0.0
    catastrophic = wn > 2 * wb
    fails = []
    if degenerate:
        fails.append(f"degenerate {degenerate}/{len(SPOT_PROMPTS)}")
    if catastrophic:
        fails.append("CATASTROPHIC rKL blow-up")
    if with_param and mal_rate > R1_MALFORMED_REF:
        fails.append(f"malformed-rate {mal_rate:.0%} > r1 ref {R1_MALFORMED_REF:.0%}")
    verdict = "FAIL" if fails else "PASS"
    print(f"\n=== GATE: {verdict} ===  rKL delta {reduction:+.1%} (directional); "
          f"malformed {malformed}/{with_param} param-emitting completions; "
          f"degenerate {degenerate}/{len(SPOT_PROMPTS)}"
          f"{'; ' + '; '.join(fails) if fails else ''}")


if __name__ == "__main__":
    asyncio.run(main())
