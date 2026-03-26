"""
Modal training + inference for the Kaetram World Model.

Trains the Small Transformer Forward Dynamics Model on a GPU in the cloud,
then provides an inference endpoint for MCTS planning.

Usage:
    # First time: authenticate with Modal
    modal setup

    # Train (uploads data, trains on T4/L4, saves checkpoint)
    modal run world/train_modal.py

    # Run MCTS inference test with the trained model
    modal run world/train_modal.py::run_mcts

    # Download trained checkpoint locally
    modal volume get kaetram-world-vol /checkpoints/best.pt ./world/checkpoints/best.pt
"""

import modal

# ---------------------------------------------------------------------------
# Modal setup
# ---------------------------------------------------------------------------

app = modal.App("kaetram-world-model")

# Persistent volume for checkpoints
world_vol = modal.Volume.from_name("kaetram-world-vol", create_if_missing=True)

# Container image — PyTorch + dependencies (lightweight, no LLM libs needed)
world_image = (
    modal.Image.debian_slim(python_version="3.11")
    .uv_pip_install(
        "torch>=2.1.0",
        "numpy>=1.24.0",
        "tqdm>=4.65.0",
        "onnxruntime>=1.16.0",
    )
)

# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

@app.function(
    image=world_image,
    gpu="T4",  # 16GB VRAM — plenty for 2.3M param model, ~$0.20/hr
    timeout=1800,  # 30 min max (training takes 5-15 min)
    volumes={"/checkpoints": world_vol},
)
def train(transitions_data: bytes, epochs: int = 50, batch_size: int = 256):
    """Train the world model on a Modal T4 GPU."""
    import json
    import time
    import tempfile
    from pathlib import Path

    import numpy as np
    import torch
    import torch.nn as nn
    from torch.utils.data import Dataset, DataLoader, random_split
    from tqdm import tqdm

    # ── Write transition data to temp file ──
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    tmp.write(transitions_data.decode("utf-8"))
    tmp.close()

    # ── Import world model code (copy source into container) ──
    # Since world/ is a local package, we inline the critical components
    # that were already tested locally.

    # --- Schema constants (lean 16-dim combat-focused) ---
    STATE_DIM = 16
    ACTION_DIM = 26
    NUM_ACTIONS = 22
    MAX_ENTITIES = 3

    ACTION_TYPES = [
        "attack", "navigate", "eat", "equip", "interact_npc", "loot",
        "warp", "wait", "click", "click_entity", "click_tile", "move",
        "talk_npc", "respawn", "quest_accept", "heal", "set_style",
        "reconnect", "stuck_reset", "clear_combat", "nav_cancel", "other",
    ]
    ACTION_TO_IDX = {a: i for i, a in enumerate(ACTION_TYPES)}

    HP_MAX, XP_MAX, LEVEL_MAX, DIST_MAX = 1000.0, 100000.0, 100.0, 20.0
    POS_MAX = 500.0

    def _num(val, default=0):
        if isinstance(val, (int, float)): return float(val)
        if isinstance(val, str):
            try: return float(val)
            except ValueError: return float(default)
        return float(default)

    def encode_state(state):
        vec = np.zeros(STATE_DIM, dtype=np.float32)
        ps = state.get("player_stats", state)
        if not isinstance(ps, dict): ps = state
        max_hp = max(_num(ps.get("max_hp", ps.get("maxHp", 100)), 100), 1)
        vec[0] = _num(ps.get("hp", ps.get("hitpoints", 0))) / max_hp
        vec[1] = max_hp / HP_MAX
        vec[2] = _num(ps.get("level", 1), 1) / LEVEL_MAX
        vec[3] = _num(ps.get("experience", ps.get("exp", 0))) / XP_MAX

        entities = state.get("nearby_entities", state.get("entities", []))
        if isinstance(entities, list):
            sorted_ents = sorted(
                [e for e in entities if isinstance(e, dict)],
                key=lambda e: _num(e.get("distance", 999), 999)
            )[:MAX_ENTITIES]
            for i, ent in enumerate(sorted_ents):
                base = 4 + i * 3
                ent_hp = _num(ent.get("hp", ent.get("hitpoints", 0)))
                ent_max = _num(ent.get("max_hp", ent.get("maxHitpoints", max(ent_hp, 1))), max(ent_hp, 1))
                vec[base] = ent_hp / max(ent_max, 1)
                vec[base + 1] = min(_num(ent.get("distance", 20), 20), DIST_MAX) / DIST_MAX
                vec[base + 2] = 1.0 if ent.get("is_aggressive", ent.get("aggressive", False)) else 0.0

        ui = state.get("ui_state", {})
        if not isinstance(ui, dict): ui = {}
        vec[13] = 1.0 if ui.get("is_dead", state.get("is_dead", False)) else 0.0
        vec[14] = 1.0 if ui.get("is_poisoned", state.get("poisoned", False)) else 0.0
        vec[15] = 1.0 if ui.get("has_target", state.get("has_target", False)) else 0.0
        return vec

    def encode_action(action_type, args=None):
        vec = np.zeros(ACTION_DIM, dtype=np.float32)
        idx = ACTION_TO_IDX.get(action_type, ACTION_TO_IDX["other"])
        vec[idx] = 1.0
        if args:
            vec[NUM_ACTIONS] = args.get("target_idx", 0) / MAX_ENTITIES
            vec[NUM_ACTIONS + 1] = args.get("x", 0) / POS_MAX
            vec[NUM_ACTIONS + 2] = args.get("y", 0) / POS_MAX
            vec[NUM_ACTIONS + 3] = args.get("slot", 0) / 30.0
        return vec

    # --- Model ---
    import math

    class PositionalEncoding(nn.Module):
        def __init__(self, d_model, max_len=128):
            super().__init__()
            pe = torch.zeros(max_len, d_model)
            position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
            div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
            pe[:, 0::2] = torch.sin(position * div_term)
            pe[:, 1::2] = torch.cos(position * div_term)
            self.register_buffer("pe", pe.unsqueeze(0))
        def forward(self, x):
            return x + self.pe[:, :x.size(1)]

    class KaetramWorldModel(nn.Module):
        def __init__(self, d_model=256, n_layers=4, n_heads=4, d_ff=512, dropout=0.1):
            super().__init__()
            self.d_model = d_model
            self.patch_size = 16
            input_dim = STATE_DIM + ACTION_DIM
            n_patches = math.ceil(input_dim / self.patch_size)
            self.patch_proj = nn.Linear(self.patch_size, d_model)
            self.pos_enc = PositionalEncoding(d_model, max_len=n_patches + 1)
            self.cls_token = nn.Parameter(torch.randn(1, 1, d_model) * 0.02)
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=n_heads, dim_feedforward=d_ff,
                dropout=dropout, activation="gelu", batch_first=True, norm_first=True,
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
            self.delta_head = nn.Sequential(nn.Linear(d_model, d_model), nn.GELU(), nn.Dropout(dropout), nn.Linear(d_model, STATE_DIM))
            self.reward_head = nn.Sequential(nn.Linear(d_model, d_model // 2), nn.GELU(), nn.Linear(d_model // 2, 1))
            self.terminal_head = nn.Sequential(nn.Linear(d_model, d_model // 2), nn.GELU(), nn.Linear(d_model // 2, 1))
            self._init_weights()

        def _init_weights(self):
            for m in self.modules():
                if isinstance(m, nn.Linear):
                    nn.init.xavier_uniform_(m.weight)
                    if m.bias is not None: nn.init.zeros_(m.bias)

        def _patchify(self, x):
            B, D = x.shape
            pad = (self.patch_size - D % self.patch_size) % self.patch_size
            if pad > 0: x = torch.nn.functional.pad(x, (0, pad))
            return self.patch_proj(x.view(B, -1, self.patch_size))

        def forward(self, state, action):
            x = torch.cat([state, action], dim=-1)
            patches = self._patchify(x)
            B = patches.size(0)
            tokens = torch.cat([self.cls_token.expand(B, -1, -1), patches], dim=1)
            tokens = self.pos_enc(tokens)
            encoded = self.encoder(tokens)
            cls_out = encoded[:, 0]
            return self.delta_head(cls_out), self.reward_head(cls_out), torch.sigmoid(self.terminal_head(cls_out))

    # --- Dataset ---
    class TransitionDataset(Dataset):
        def __init__(self, jsonl_path):
            self.records = []
            with open(jsonl_path) as f:
                for line in f:
                    try: rec = json.loads(line)
                    except json.JSONDecodeError: continue
                    state_vec = encode_state(rec.get("state", {}))
                    next_state_vec = encode_state(rec.get("next_state", {}))
                    action_vec = encode_action(rec.get("action", "other"), rec.get("action_args", {}))
                    delta_vec = next_state_vec - state_vec
                    reward = rec.get("delta", {}).get("xp_delta", 0) / XP_MAX
                    terminal = 1.0 if rec.get("delta", {}).get("died", False) else 0.0
                    self.records.append((state_vec, action_vec, delta_vec, np.float32(reward), np.float32(terminal)))
            print(f"Loaded {len(self.records)} transitions")

        def __len__(self): return len(self.records)
        def __getitem__(self, idx):
            s, a, d, r, t = self.records[idx]
            return torch.from_numpy(s), torch.from_numpy(a), torch.from_numpy(d), torch.tensor(r), torch.tensor(t)

    # ── Training loop ──
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    dataset = TransitionDataset(tmp.name)
    if len(dataset) == 0:
        return {"error": "No transitions loaded"}

    n_val = max(1, int(len(dataset) * 0.1))
    n_train = len(dataset) - n_val
    train_ds, val_ds = random_split(dataset, [n_train, n_val], generator=torch.Generator().manual_seed(42))
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, pin_memory=True)
    print(f"Train: {n_train} | Val: {n_val}")

    model = KaetramWorldModel().to(device)
    print(f"Model params: {sum(p.numel() for p in model.parameters()):,}")

    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-4)
    warmup_steps = min(500, len(train_loader) * 2)
    total_steps = len(train_loader) * epochs
    def lr_lambda(step):
        if step < warmup_steps: return step / max(warmup_steps, 1)
        return 0.5 * (1.0 + np.cos(np.pi * (step - warmup_steps) / max(total_steps - warmup_steps, 1)))
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

    mse_loss = nn.MSELoss()
    bce_loss = nn.BCELoss()

    best_val_loss = float("inf")
    patience_counter = 0
    start = time.time()

    for epoch in range(epochs):
        model.train()
        train_losses = []
        for batch in tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}", leave=False):
            s, a, d, r, t = [b.to(device) for b in batch]
            dp, rp, tp = model(s, a)
            loss = mse_loss(dp, d) + 0.5 * mse_loss(rp.squeeze(), r) + 0.5 * bce_loss(tp.squeeze(), t)
            optimizer.zero_grad(); loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step(); scheduler.step()
            train_losses.append(loss.item())
        avg_train = np.mean(train_losses)

        model.eval()
        val_losses = []
        with torch.no_grad():
            for batch in val_loader:
                s, a, d, r, t = [b.to(device) for b in batch]
                dp, rp, tp = model(s, a)
                loss = mse_loss(dp, d) + 0.5 * mse_loss(rp.squeeze(), r) + 0.5 * bce_loss(tp.squeeze(), t)
                val_losses.append(loss.item())
        avg_val = np.mean(val_losses)

        print(f"Epoch {epoch+1:3d} | Train: {avg_train:.6f} | Val: {avg_val:.6f}")

        if avg_val < best_val_loss:
            best_val_loss = avg_val
            patience_counter = 0
            torch.save({
                "epoch": epoch + 1, "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(), "val_loss": best_val_loss,
                "args": {"d_model": 256, "n_layers": 4, "n_heads": 4, "d_ff": 512},
            }, "/checkpoints/best.pt")
            print(f"  ✓ Saved checkpoint (val_loss={best_val_loss:.6f})")
        else:
            patience_counter += 1
            if patience_counter >= 5:
                print(f"Early stopping at epoch {epoch+1}")
                break

    elapsed = time.time() - start
    world_vol.commit()

    metrics = {"best_val_loss": float(best_val_loss), "epochs_run": int(epoch + 1), "runtime_s": float(elapsed), "n_transitions": len(dataset)}
    print(f"\nTraining complete in {elapsed:.0f}s | Best val loss: {best_val_loss:.6f}")
    print(f"Checkpoint saved to Modal volume 'kaetram-world-vol' at /checkpoints/best.pt")
    print(f"\nDownload: modal volume get kaetram-world-vol /checkpoints/best.pt ./world/checkpoints/best.pt")
    return metrics


# ---------------------------------------------------------------------------
# MCTS Inference (optional — test the trained model)
# ---------------------------------------------------------------------------

@app.function(
    image=world_image,
    gpu="T4",
    timeout=300,
    volumes={"/checkpoints": world_vol},
)
def run_mcts_remote(game_state_json: str, n_simulations: int = 200):
    """Run MCTS planning on a Modal GPU using the trained world model."""
    import json
    import torch

    # This would use the same inlined model + MCTS code
    # For now, just verify the checkpoint loads
    ckpt = torch.load("/checkpoints/best.pt", map_location="cuda", weights_only=False)
    print(f"Loaded checkpoint: epoch={ckpt['epoch']}, val_loss={ckpt['val_loss']:.6f}")
    return {"status": "ok", "epoch": ckpt["epoch"], "val_loss": ckpt["val_loss"]}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main():
    """Extract transitions locally, upload, and train on Modal."""
    import os
    import subprocess
    import sys

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    transitions_path = os.path.join(project_dir, "dataset", "world_model", "transitions.jsonl")

    # Auto-extract if not present
    if not os.path.exists(transitions_path):
        print("Transitions not found. Extracting from raw logs...")
        subprocess.run([
            sys.executable, "-m", "world.extract_transitions",
            "--log-dir", os.path.join(project_dir, "dataset", "raw"),
            "--output", transitions_path,
        ], cwd=project_dir, check=True)

    with open(transitions_path, "rb") as f:
        data = f.read()

    n_lines = data.count(b"\n")
    print(f"Uploading {n_lines} transitions ({len(data):,} bytes)...")
    print("Launching training on Modal T4 GPU...")

    metrics = train.remote(data)

    print(f"\n{'='*60}")
    print("TRAINING COMPLETE")
    print(f"{'='*60}")
    print(f"  Best val loss: {metrics.get('best_val_loss', '?'):.6f}")
    print(f"  Epochs:        {metrics.get('epochs_run', '?')}")
    print(f"  Runtime:       {metrics.get('runtime_s', 0):.0f}s")
    print(f"  Transitions:   {metrics.get('n_transitions', '?')}")
    print(f"\nDownload checkpoint:")
    print(f"  modal volume get kaetram-world-vol /checkpoints/best.pt ./world/checkpoints/best.pt")
