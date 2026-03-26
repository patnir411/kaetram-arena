#!/usr/bin/env python3
"""
train.py — Train the Kaetram World Model locally on RTX 3060.

Trains a small Transformer to predict (state, action) → (delta, reward, terminal)
from extracted transition data.

Usage:
    python -m world.train --data dataset/world_model/transitions.jsonl
    python -m world.train --data dataset/world_model/transitions.jsonl --epochs 50 --overfit-test
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, random_split
from tqdm import tqdm

from world.schema import StateEncoder, ActionEncoder, STATE_DIM, ACTION_DIM
from world.model import KaetramWorldModel


class TransitionDataset(Dataset):
    """Dataset of (state, action, delta, reward, terminal) tuples."""

    def __init__(self, jsonl_path: str):
        self.state_enc = StateEncoder()
        self.action_enc = ActionEncoder()
        self.records = []

        print(f"Loading transitions from {jsonl_path}...")
        with open(jsonl_path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                state = rec.get("state", {})
                next_state = rec.get("next_state", {})
                action_type = rec.get("action", "other")
                action_args = rec.get("action_args", {})
                delta = rec.get("delta", {})

                # Encode
                state_vec = self.state_enc.encode(state)
                next_state_vec = self.state_enc.encode(next_state)
                action_vec = self.action_enc.encode(action_type, action_args)

                # Delta = next_state_vec - state_vec (what changed)
                delta_vec = next_state_vec - state_vec

                # Reward = XP gained (normalized)
                reward = delta.get("xp_delta", 0) / StateEncoder.XP_MAX

                # Terminal = died
                terminal = 1.0 if delta.get("died", False) else 0.0

                self.records.append({
                    "state": state_vec,
                    "action": action_vec,
                    "delta": delta_vec,
                    "reward": np.float32(reward),
                    "terminal": np.float32(terminal),
                })

        print(f"Loaded {len(self.records)} transitions")

    def __len__(self):
        return len(self.records)

    def __getitem__(self, idx):
        rec = self.records[idx]
        return (
            torch.from_numpy(rec["state"]),
            torch.from_numpy(rec["action"]),
            torch.from_numpy(rec["delta"]),
            torch.tensor(rec["reward"]),
            torch.tensor(rec["terminal"]),
        )


def train(args):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # Load data
    dataset = TransitionDataset(args.data)
    if len(dataset) == 0:
        print("ERROR: No transitions loaded. Run extract_transitions.py first.")
        return

    # Split
    n_val = max(1, int(len(dataset) * 0.1))
    n_train = len(dataset) - n_val
    train_ds, val_ds = random_split(dataset, [n_train, n_val],
                                     generator=torch.Generator().manual_seed(42))

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                              num_workers=0, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False,
                            num_workers=0, pin_memory=True)

    print(f"Train: {n_train} | Val: {n_val}")

    # Model
    model = KaetramWorldModel(
        d_model=args.d_model,
        n_layers=args.n_layers,
        n_heads=args.n_heads,
        d_ff=args.d_ff,
        dropout=args.dropout,
    ).to(device)
    print(f"Model params: {model.num_params:,}")

    # Optimizer
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)

    # LR scheduler: cosine with warmup
    warmup_steps = min(500, len(train_loader) * 2)
    total_steps = len(train_loader) * args.epochs

    def lr_lambda(step):
        if step < warmup_steps:
            return step / max(warmup_steps, 1)
        progress = (step - warmup_steps) / max(total_steps - warmup_steps, 1)
        return 0.5 * (1.0 + np.cos(np.pi * progress))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

    # Loss functions
    mse_loss = nn.MSELoss()
    bce_loss = nn.BCELoss()

    # Training loop
    checkpoint_dir = Path(args.checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    best_val_loss = float("inf")
    patience_counter = 0
    train_start = time.time()

    for epoch in range(args.epochs):
        # ── Train ──
        model.train()
        train_losses = []

        for batch in tqdm(train_loader, desc=f"Epoch {epoch+1}/{args.epochs}", leave=False):
            state, action, delta_target, reward_target, terminal_target = [b.to(device) for b in batch]

            delta_pred, reward_pred, terminal_pred = model(state, action)

            loss_delta = mse_loss(delta_pred, delta_target)
            loss_reward = mse_loss(reward_pred.squeeze(), reward_target)
            loss_terminal = bce_loss(terminal_pred.squeeze(), terminal_target)

            # Weighted multi-task loss
            loss = loss_delta + 0.5 * loss_reward + 0.5 * loss_terminal

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()

            train_losses.append(loss.item())

        avg_train = np.mean(train_losses)

        # ── Validate ──
        model.eval()
        val_losses = []
        val_delta_losses = []

        with torch.no_grad():
            for batch in val_loader:
                state, action, delta_target, reward_target, terminal_target = [b.to(device) for b in batch]
                delta_pred, reward_pred, terminal_pred = model(state, action)

                loss_delta = mse_loss(delta_pred, delta_target)
                loss_reward = mse_loss(reward_pred.squeeze(), reward_target)
                loss_terminal = bce_loss(terminal_pred.squeeze(), terminal_target)
                loss = loss_delta + 0.5 * loss_reward + 0.5 * loss_terminal

                val_losses.append(loss.item())
                val_delta_losses.append(loss_delta.item())

        avg_val = np.mean(val_losses)
        avg_val_delta = np.mean(val_delta_losses)
        lr = optimizer.param_groups[0]["lr"]

        print(f"Epoch {epoch+1:3d} | Train: {avg_train:.6f} | Val: {avg_val:.6f} | "
              f"Val Δ: {avg_val_delta:.6f} | LR: {lr:.2e}")

        # ── Checkpointing ──
        if avg_val < best_val_loss:
            best_val_loss = avg_val
            patience_counter = 0
            torch.save({
                "epoch": epoch + 1,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_loss": best_val_loss,
                "args": vars(args),
            }, checkpoint_dir / "best.pt")
            print(f"  ✓ Saved best checkpoint (val_loss={best_val_loss:.6f})")
        else:
            patience_counter += 1
            if patience_counter >= args.patience:
                print(f"\nEarly stopping after {args.patience} epochs without improvement.")
                break

        # Overfit test: stop when loss is very low
        if args.overfit_test and avg_train < 0.001:
            print(f"\n✓ Overfit test passed! Train loss < 0.001 at epoch {epoch+1}")
            break

    elapsed = time.time() - train_start
    print(f"\nTraining complete in {elapsed:.0f}s")
    print(f"Best val loss: {best_val_loss:.6f}")
    print(f"Checkpoint: {checkpoint_dir / 'best.pt'}")

    # Export ONNX
    if args.export_onnx:
        export_onnx(model, device, checkpoint_dir / "world_model.onnx")


def export_onnx(model, device, path):
    """Export model to ONNX for fast inference."""
    model.eval()
    dummy_state = torch.randn(1, STATE_DIM).to(device)
    dummy_action = torch.randn(1, ACTION_DIM).to(device)

    torch.onnx.export(
        model,
        (dummy_state, dummy_action),
        str(path),
        input_names=["state", "action"],
        output_names=["delta", "reward", "terminal"],
        dynamic_axes={
            "state": {0: "batch"},
            "action": {0: "batch"},
            "delta": {0: "batch"},
            "reward": {0: "batch"},
            "terminal": {0: "batch"},
        },
        opset_version=17,
    )
    print(f"Exported ONNX: {path}")


def main():
    parser = argparse.ArgumentParser(description="Train Kaetram World Model")
    parser.add_argument("--data", type=str, required=True,
                        help="Path to transitions.jsonl")
    parser.add_argument("--checkpoint-dir", type=str, default="world/checkpoints",
                        help="Directory for model checkpoints")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--d-model", type=int, default=256)
    parser.add_argument("--n-layers", type=int, default=4)
    parser.add_argument("--n-heads", type=int, default=4)
    parser.add_argument("--d-ff", type=int, default=512)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--patience", type=int, default=5,
                        help="Early stopping patience")
    parser.add_argument("--overfit-test", action="store_true",
                        help="Stop when train loss < 0.001 (debugging)")
    parser.add_argument("--export-onnx", action="store_true",
                        help="Export ONNX after training")
    args = parser.parse_args()

    train(args)


if __name__ == "__main__":
    main()
