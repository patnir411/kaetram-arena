#!/usr/bin/env python3
"""
evaluate.py — Evaluate the trained Kaetram World Model.

Measures per-field accuracy, multi-step rollout drift, and terminal prediction AUC.

Usage:
    python -m world.evaluate --model world/checkpoints/best.pt --data dataset/world_model/transitions.jsonl
"""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from tqdm import tqdm

from world.schema import StateEncoder, ActionEncoder, STATE_DIM, ACTION_DIM
from world.model import KaetramWorldModel
from world.train import TransitionDataset


# Field names for human-readable reporting
FIELD_NAMES = [
    "hp_ratio", "max_hp", "level", "xp", "pos_x", "pos_y",
    # entities (5 × 4)
    *[f"ent{i}_{f}" for i in range(5) for f in ("type", "hp", "dist", "aggr")],
    # equipment (5)
    *[f"equip_{k}" for k in ("weapon", "armor", "pendant", "ring", "boots")],
    # inventory
    "inv_count",
    # quests (3 × 2)
    *[f"quest{i}_{f}" for i in range(3) for f in ("id", "stage")],
    # UI flags
    "is_dead", "is_poisoned", "has_target",
    # padding
    "pad0", "pad1", "pad2",
]


def evaluate(args):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Load model
    print(f"Loading model from {args.model}...")
    ckpt = torch.load(args.model, map_location=device, weights_only=False)
    model_args = ckpt.get("args", {})

    model = KaetramWorldModel(
        d_model=model_args.get("d_model", 256),
        n_layers=model_args.get("n_layers", 4),
        n_heads=model_args.get("n_heads", 4),
        d_ff=model_args.get("d_ff", 512),
    ).to(device)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()

    # Load data
    dataset = TransitionDataset(args.data)
    loader = torch.utils.data.DataLoader(dataset, batch_size=256, shuffle=False)

    print(f"\n{'='*60}")
    print(f"EVALUATION REPORT")
    print(f"{'='*60}")
    print(f"Model params: {model.num_params:,}")
    print(f"Checkpoint epoch: {ckpt.get('epoch', '?')}")
    print(f"Checkpoint val_loss: {ckpt.get('val_loss', '?'):.6f}")
    print(f"Dataset: {len(dataset)} transitions")

    # ── Per-field accuracy ──
    print(f"\n--- Per-Field Accuracy (ε=0.05) ---")
    all_delta_pred = []
    all_delta_true = []
    all_terminal_pred = []
    all_terminal_true = []

    with torch.no_grad():
        for batch in tqdm(loader, desc="Evaluating"):
            state, action, delta_target, reward_target, terminal_target = [b.to(device) for b in batch]
            delta_pred, reward_pred, terminal_pred = model(state, action)

            all_delta_pred.append(delta_pred.cpu().numpy())
            all_delta_true.append(delta_target.cpu().numpy())
            all_terminal_pred.append(terminal_pred.squeeze().cpu().numpy())
            all_terminal_true.append(terminal_target.cpu().numpy())

    all_delta_pred = np.concatenate(all_delta_pred, axis=0)
    all_delta_true = np.concatenate(all_delta_true, axis=0)
    all_terminal_pred = np.concatenate(all_terminal_pred, axis=0)
    all_terminal_true = np.concatenate(all_terminal_true, axis=0)

    eps = 0.05
    per_field_acc = np.mean(np.abs(all_delta_pred - all_delta_true) < eps, axis=0)

    # Group fields for readable output
    groups = {
        "Player Stats": (0, 6),
        "Entities": (6, 26),
        "Equipment": (26, 31),
        "Inventory": (31, 32),
        "Quests": (32, 38),
        "UI Flags": (38, 41),
    }

    for group_name, (start, end) in groups.items():
        group_acc = np.mean(per_field_acc[start:end])
        print(f"  {group_name:20s}: {group_acc*100:6.1f}%")

        if args.verbose:
            for i in range(start, min(end, len(FIELD_NAMES))):
                print(f"    {FIELD_NAMES[i]:25s}: {per_field_acc[i]*100:6.1f}%")

    overall_acc = np.mean(per_field_acc)
    print(f"  {'OVERALL':20s}: {overall_acc*100:6.1f}%")

    # ── Zero-delta accuracy ──
    print(f"\n--- Zero-Delta Detection ---")
    zero_mask = np.abs(all_delta_true) < 1e-6
    if zero_mask.sum() > 0:
        zero_correct = np.abs(all_delta_pred[zero_mask]) < eps
        zero_acc = np.mean(zero_correct)
        print(f"  When true delta=0, predicted near-zero: {zero_acc*100:.1f}%")
        print(f"  (Out of {zero_mask.sum():,} zero-delta fields)")

    # ── Terminal prediction ──
    print(f"\n--- Terminal (Death) Prediction ---")
    n_deaths = int(all_terminal_true.sum())
    print(f"  Deaths in dataset: {n_deaths} / {len(all_terminal_true)}")
    if n_deaths > 0:
        death_pred_at_death = all_terminal_pred[all_terminal_true > 0.5]
        print(f"  Avg death probability when actually dying: {np.mean(death_pred_at_death):.3f}")
        alive_pred = all_terminal_pred[all_terminal_true < 0.5]
        print(f"  Avg death probability when alive: {np.mean(alive_pred):.3f}")

    # ── Multi-step rollout drift ──
    if args.rollout_steps > 0:
        print(f"\n--- Multi-Step Rollout Drift ({args.rollout_steps} steps) ---")
        eval_multi_step(model, dataset, device, args.rollout_steps)


def eval_multi_step(model, dataset, device, max_steps: int):
    """Evaluate error accumulation by feeding predictions back as input."""
    state_enc = StateEncoder()
    action_enc = ActionEncoder()

    # Take first 100 consecutive transitions as a trajectory
    n_traj = min(100, len(dataset))
    errors_by_step = {i: [] for i in range(1, max_steps + 1)}

    for start_idx in range(0, n_traj - max_steps, max_steps):
        # Initialize with real state
        state_vec, action_vec, _, _, _ = dataset[start_idx]
        current_state = state_vec.unsqueeze(0).to(device)

        for step in range(max_steps):
            idx = start_idx + step
            if idx >= len(dataset):
                break

            _, action_vec, delta_true, _, _ = dataset[idx]
            action = action_vec.unsqueeze(0).to(device)

            with torch.no_grad():
                delta_pred, _, _ = model(current_state, action)

            # Error at this step
            error = torch.mean(torch.abs(delta_pred.cpu() - delta_true.unsqueeze(0))).item()
            errors_by_step[step + 1].append(error)

            # Feed prediction forward (accumulated error)
            current_state = current_state + delta_pred

    for step in range(1, max_steps + 1):
        if errors_by_step[step]:
            mean_err = np.mean(errors_by_step[step])
            print(f"  Step {step:2d}: MAE = {mean_err:.4f}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate Kaetram World Model")
    parser.add_argument("--model", type=str, required=True, help="Path to checkpoint")
    parser.add_argument("--data", type=str, required=True, help="Path to transitions.jsonl")
    parser.add_argument("--rollout-steps", type=int, default=10, help="Multi-step rollout depth")
    parser.add_argument("--verbose", action="store_true", help="Show per-field breakdown")
    args = parser.parse_args()

    evaluate(args)


if __name__ == "__main__":
    main()
