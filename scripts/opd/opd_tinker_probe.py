"""Phase-0 Tinker OPD probe — base-9B student, 27B dense teacher.

Runs one short sequence through the full reverse-KL OPD primitive set to de-risk
the experiment before any real round:
  - student (Qwen3.5-9B) compute_logprobs  -> behavior logprobs
  - teacher (Qwen3.5-27B) compute_logprobs -> teacher logprobs
  - reverse_kl = (logp_student - logp_teacher)*mask ; advantage = -coef*reverse_kl
  - forward_backward(loss_fn="importance_sampling") + optim_step on the 9B LoRA

Asserts: logprob length/offset alignment, finite sane reverse-KL, the loss/step
run, and <think> survives the tokenizer round-trip. Reads telemetry before/after
to expose what was billed. Cost: pennies (one tiny sequence).
"""
import math
import tinker
import torch

STUDENT_MODEL = "Qwen/Qwen3.5-9B"
TEACHER_MODEL = "Qwen/Qwen3.5-27B"
KL_COEF = 1.0

# A representative agent turn: scaffolded context + an assistant action carrying
# <think> reasoning and a tool_call (the shape the student/teacher both score).
PROMPT = (
    "<|im_start|>system\nYou are a Kaetram agent. Advance Core-3 quests.<|im_end|>\n"
    "<|im_start|>user\nobserve: Foraging 4, near Oak trees. Herbalist's stage 1/3 "
    "needs Blue Lily (Foraging 5).<|im_end|>\n<|im_start|>assistant\n"
)
COMPLETION = (
    "<think>\nForaging is 4, Blue Lily needs 5. Keep grinding Oak to level up "
    "before switching to lilies.\n</think>\n"
    "<tool_call>\n<function=gather>\n<parameter=resource>Oak</parameter>\n"
    "</function>\n</tool_call><|im_end|>"
)


def _fmt_telemetry(t):
    if t is None:
        return "None"
    try:
        return t.model_dump()
    except Exception:
        return {a: getattr(t, a) for a in dir(t) if not a.startswith("_") and not callable(getattr(t, a))}


def main():
    sc = tinker.ServiceClient()
    print(f"telemetry @ start: {_fmt_telemetry(sc.get_telemetry())}\n")

    train = sc.create_lora_training_client(base_model=STUDENT_MODEL, rank=32)
    student = sc.create_sampling_client(base_model=STUDENT_MODEL)
    teacher = sc.create_sampling_client(base_model=TEACHER_MODEL)
    tok = train.get_tokenizer()

    prompt_ids = tok.encode(PROMPT, add_special_tokens=False)
    comp_ids = tok.encode(COMPLETION, add_special_tokens=False)
    all_ids = prompt_ids + comp_ids
    n_prompt, n_all = len(prompt_ids), len(all_ids)
    print(f"tokens: prompt={n_prompt} completion={len(comp_ids)} total={n_all}")

    # Render parity: <think> must survive the tokenizer round-trip.
    decoded = tok.decode(comp_ids)
    assert "<think>" in decoded and "</think>" in decoded, "RENDER PARITY FAIL: <think> lost"
    assert "<tool_call>" in decoded, "RENDER PARITY FAIL: <tool_call> lost"
    print("render parity: <think> + <tool_call> survive round-trip  OK")

    # Per-token logprobs over the FULL sequence; [0] is None (no left context).
    full = tinker.ModelInput.from_ints(all_ids)
    student_lp = student.compute_logprobs(full).result()
    teacher_lp = teacher.compute_logprobs(full).result()
    assert len(student_lp) == n_all == len(teacher_lp), "logprob length mismatch"
    print(f"logprobs: student[0]={student_lp[0]} teacher[0]={teacher_lp[0]} (expect None)")

    # Targets = all_ids[1:]; behavior/teacher logprobs aligned as lp[1:].
    targets = all_ids[1:]
    s_lp = [x if x is not None else 0.0 for x in student_lp[1:]]
    t_lp = [x if x is not None else 0.0 for x in teacher_lp[1:]]
    # mask: 1 on completion target positions only (target index i -> token all_ids[i+1])
    mask = [1.0 if (i + 1) >= n_prompt else 0.0 for i in range(len(targets))]

    s = torch.tensor(s_lp)
    t = torch.tensor(t_lp)
    m = torch.tensor(mask)
    reverse_kl = (s - t) * m
    advantages = (-KL_COEF * reverse_kl)

    n_action = int(m.sum().item())
    mean_rkl = float(reverse_kl.sum() / max(n_action, 1))
    print(f"action tokens (masked): {n_action}")
    print(f"mean reverse-KL over action tokens (logp_s - logp_t): {mean_rkl:.4f}")
    assert all(math.isfinite(v) for v in (mean_rkl,)), "non-finite reverse-KL"
    assert n_action > 0, "no action tokens masked in"

    # importance_sampling accepts {target_tokens, logprobs, advantages}; masking is
    # carried by advantages (already 0 on non-action tokens via reverse_kl * mask).
    datum = tinker.Datum(
        model_input=tinker.ModelInput.from_ints(all_ids[:-1]),
        loss_fn_inputs={
            "target_tokens": tinker.TensorData.from_torch(torch.tensor(targets)),
            "logprobs": tinker.TensorData.from_torch(s),
            "advantages": tinker.TensorData.from_torch(advantages),
        },
    )
    fb = train.forward_backward([datum], loss_fn="importance_sampling").result()
    try:
        fb_dump = fb.model_dump()
    except Exception:
        fb_dump = {a: getattr(fb, a) for a in dir(fb) if not a.startswith("_") and not callable(getattr(fb, a))}
    print(f"forward_backward OK -> {fb_dump}")

    step = train.optim_step(tinker.AdamParams(learning_rate=1e-4)).result()
    print(f"optim_step OK -> {type(step).__name__}")

    print(f"\ntelemetry @ end: {_fmt_telemetry(sc.get_telemetry())}")
    print("\nPHASE-0 PROBE PASSED")


if __name__ == "__main__":
    main()
