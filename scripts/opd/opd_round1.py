"""r11 OPD round 1 — offline reverse-KL distillation from existing base-9B+scaffold rollouts.

Student = Qwen3.5-9B (Tinker LoRA). Teacher = Qwen3.5-27B (dense), R11 scaffold.
The June-3 base-9B+scaffold runs ARE the student's own on-policy distribution, so
round 1 reuses them (no new rollouts): reconstruct each session into a Qwen-rendered
token sequence + an assistant-token mask, score with student + teacher compute_logprobs,
and apply the canonical reverse-KL update:

    reverse_kl = (logp_student - logp_teacher) * mask     # per action token
    advantage  = -kl_coef * reverse_kl
    forward_backward(loss_fn="importance_sampling")

`--dry-run` does everything except the Tinker calls (free): renders, masks, and prints
a decoded sample + token/mask stats so render parity can be eyeballed before any spend.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(HERE), str(REPO), str(REPO / "scripts" / "log_analysis"), str(REPO / "finetune")):
    if p not in sys.path:
        sys.path.insert(0, p)

from opd_probe import reconstruct_session  # noqa: E402

STUDENT_MODEL = "Qwen/Qwen3.5-9B"
TEACHER_MODEL = "Qwen/Qwen3.5-27B"
RUN_IDS = ["run_20260603_175221", "run_20260603_235259"]
ASSISTANT_HEADER = "<|im_start|>assistant\n"
IM_END = "<|im_end|>"


def load_tokenizer():
    """Local Qwen3.5 tokenizer + patched template (<think> preserved). All Qwen3.5
    sizes share this tokenizer (verified), so it matches the Tinker models exactly."""
    from transformers import AutoTokenizer
    from render import patch_qwen_chat_template
    last_err = None
    for name in ("Qwen/Qwen3.5-9B", "unsloth/Qwen3.5-9B"):
        try:
            tok = AutoTokenizer.from_pretrained(name)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    else:
        raise RuntimeError(f"could not load a Qwen3.5 tokenizer: {last_err}")
    patch_qwen_chat_template(tok)
    assert tok.is_fast, "need a fast tokenizer for offset-mapping masks"
    return tok


_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_TOOLCALL_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)


def turn_to_chat(turn) -> dict:
    """Parse the raw logged generation (turn.text carries inline <think> and
    <tool_call> XML) back into the canonical training-render triple
    (reasoning_content, content, structured tool_calls), so the patched Qwen
    template renders <think> and the tool_call exactly once each."""
    raw = turn.text or ""
    reasoning = turn.thinking or ""
    m = _THINK_RE.search(raw)
    if m and not reasoning:
        reasoning = m.group(1).strip()
    raw = _TOOLCALL_RE.sub("", _THINK_RE.sub("", raw))
    msg = {"role": "assistant", "content": raw.strip()}
    if reasoning:
        msg["reasoning_content"] = reasoning
    if turn.tool_calls:
        msg["tool_calls"] = turn.tool_calls
    return msg


def build_messages(log_path: Path):
    base_messages, turns = reconstruct_session(log_path)
    if not turns:
        return None
    messages = list(base_messages)
    for turn, results in turns:
        messages.append(turn_to_chat(turn))
        for tr in results:
            messages.append({"role": "tool", "content": tr.result_str, "name": tr.name})
    return messages


def _assistant_char_spans(text: str) -> list[tuple[int, int]]:
    """Char spans of assistant-generated content: from just after each
    '<|im_start|>assistant\\n' header to the next '<|im_end|>'."""
    spans = []
    i = 0
    while True:
        h = text.find(ASSISTANT_HEADER, i)
        if h == -1:
            break
        start = h + len(ASSISTANT_HEADER)
        end = text.find(IM_END, start)
        if end == -1:
            end = len(text)
        spans.append((start, end))
        i = end + len(IM_END)
    return spans


def render_and_mask(messages, tok, max_seq: int):
    """Return (token_ids, mask, n_action) or None if empty/overlong.

    mask[i] = 1.0 iff token i lies inside an assistant-content char span."""
    text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    enc = tok(text, add_special_tokens=False, return_offsets_mapping=True)
    ids = enc["input_ids"]
    offsets = enc["offset_mapping"]
    if not ids or len(ids) > max_seq:
        return None
    spans = _assistant_char_spans(text)
    mask = []
    for (a, b) in offsets:
        in_assistant = any(a >= s and b <= e and b > a for (s, e) in spans) or \
            any(s <= a < e for (s, e) in spans)
        mask.append(1.0 if in_assistant else 0.0)
    n_action = int(sum(mask))
    if n_action == 0:
        return None
    return ids, mask, n_action


def collect_sessions(run_ids, max_sessions, seed):
    logs = []
    for run in run_ids:
        logs.extend(sorted((REPO / "dataset" / "raw").glob(f"agent_*/runs/{run}/session_*.log")))
    random.seed(seed)
    random.shuffle(logs)
    return logs[:max_sessions] if max_sessions else logs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", nargs="+", default=RUN_IDS)
    ap.add_argument("--max-sessions", type=int, default=200)
    ap.add_argument("--max-seq", type=int, default=16384)
    ap.add_argument("--kl-coef", type=float, default=1.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--batch", type=int, default=8, help="datums per forward_backward")
    ap.add_argument("--out-name", default="r11r1")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true", help="render+mask only, no Tinker calls")
    args = ap.parse_args()

    tok = load_tokenizer()
    logs = collect_sessions(args.run_ids, args.max_sessions, args.seed)
    print(f"collected {len(logs)} session logs from {args.run_ids}")

    built, skipped, tot_tokens, tot_action = [], 0, 0, 0
    for lp in logs:
        try:
            messages = build_messages(lp)
        except Exception:
            messages = None
        rm = render_and_mask(messages, tok, args.max_seq) if messages else None
        if rm is None:
            skipped += 1
            continue
        ids, mask, n_action = rm
        built.append((lp, ids, mask, n_action))
        tot_tokens += len(ids)
        tot_action += n_action

    print(f"built {len(built)} sequences ({skipped} skipped: empty/overlong/no-action)")
    print(f"total tokens={tot_tokens:,}  action(masked-in) tokens={tot_action:,}  "
          f"({100*tot_action/max(tot_tokens,1):.1f}% trainable)")

    if args.dry_run:
        if built:
            lp, ids, mask, n_action = built[0]
            text = tok.decode(ids)
            print(f"\n=== SAMPLE (decoded) {lp.parent.parent.parent.name}/{lp.name} ===")
            print(f"tokens={len(ids)} action={n_action}")
            print(text[:900])
            print("  ...[middle]...")
            print(text[-900:])
            # show a masked-region readback: decode only action tokens of first assistant span
            act_ids = [t for t, m in zip(ids, mask) if m > 0]
            print(f"\n=== first ~120 ACTION (masked-in) tokens decoded ===")
            print(tok.decode(act_ids[:120]))
        print("\nDRY-RUN OK — no Tinker calls made.")
        return

    _train(built, tok, args)


def _train(built, tok, args):
    import tinker
    import torch

    sc = tinker.ServiceClient()
    train = sc.create_lora_training_client(base_model=STUDENT_MODEL, rank=args.rank)
    student = sc.create_sampling_client(base_model=STUDENT_MODEL)
    teacher = sc.create_sampling_client(base_model=TEACHER_MODEL)
    print(f"clients up: student={STUDENT_MODEL} teacher={TEACHER_MODEL} rank={args.rank}")

    n_steps = 0
    running_kl = 0.0
    for bstart in range(0, len(built), args.batch):
        batch = built[bstart:bstart + args.batch]
        # score student + teacher on full sequences
        s_futs = [student.compute_logprobs(tinker.ModelInput.from_ints(ids)) for _, ids, _, _ in batch]
        t_futs = [teacher.compute_logprobs(tinker.ModelInput.from_ints(ids)) for _, ids, _, _ in batch]
        data = []
        kl_sum, kl_den = 0.0, 0
        for (lp, ids, mask, n_action), sf, tf in zip(batch, s_futs, t_futs):
            s_lp = sf.result()
            t_lp = tf.result()
            targets = ids[1:]
            s = torch.tensor([x if x is not None else 0.0 for x in s_lp[1:]])
            t = torch.tensor([x if x is not None else 0.0 for x in t_lp[1:]])
            m = torch.tensor(mask[1:])
            reverse_kl = (s - t) * m
            adv = -args.kl_coef * reverse_kl
            data.append(tinker.Datum(
                model_input=tinker.ModelInput.from_ints(ids[:-1]),
                loss_fn_inputs={
                    "target_tokens": tinker.TensorData.from_torch(torch.tensor(targets)),
                    "logprobs": tinker.TensorData.from_torch(s),
                    "advantages": tinker.TensorData.from_torch(adv),
                },
            ))
            kl_sum += float(reverse_kl.sum())
            kl_den += int(m.sum())
        fb = train.forward_backward(data, loss_fn="importance_sampling").result()
        train.optim_step(tinker.AdamParams(learning_rate=args.lr)).result()
        n_steps += 1
        batch_kl = kl_sum / max(kl_den, 1)
        running_kl += batch_kl
        loss = None
        try:
            loss = fb.model_dump().get("metrics", {}).get("loss:sum")
        except Exception:
            pass
        print(f"  step {n_steps:>3} | seqs {len(data)} | mean reverse-KL {batch_kl:+.4f} | loss:sum {loss}")

    print(f"\ntrained {n_steps} steps, avg reverse-KL {running_kl/max(n_steps,1):+.4f}")
    saved = train.save_state(name=args.out_name).result()
    try:
        path = saved.model_dump()
    except Exception:
        path = saved
    print(f"saved checkpoint '{args.out_name}' -> {path}")


if __name__ == "__main__":
    main()
