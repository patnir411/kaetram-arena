#!/usr/bin/env python3
"""
demo.py — Interactive demo of the Kaetram World Model.

See the model predict what happens for each action, and watch MCTS pick the best one.

Usage:
    .venv/bin/python3 -m world.demo
    .venv/bin/python3 -m world.demo --model world/checkpoints/best.pt
"""

import argparse
import torch
import numpy as np
from world.schema import StateEncoder, ActionEncoder, STATE_DIM, ACTION_DIM
from world.model import KaetramWorldModel
from world.mcts import KaetramMCTS


def demo_predictions(model, device):
    """Show what the model predicts for every action from a given state."""
    enc = StateEncoder()
    aenc = ActionEncoder()

    state = {
        "player_stats": {"hp": 50, "max_hp": 100, "level": 5, "experience": 1200},
        "player_position": {"x": 7, "y": 12},
        "nearby_entities": [
            {"name": "Rat", "type": "mob", "hp": 15, "max_hp": 15, "distance": 2, "aggressive": False},
            {"name": "Guard", "type": "npc", "hp": 100, "max_hp": 100, "distance": 5, "aggressive": False},
        ],
        "equipment": {"weapon": "Bronze Axe"},
        "inventory": [{"name": "bread"}] * 3,
        "active_quests": [{"name": "Kill Rats", "stage": 2, "stageCount": 5}],
        "ui_state": {"is_dead": False},
    }

    state_vec = enc.encode(state)
    state_t = torch.from_numpy(state_vec).unsqueeze(0).to(device)

    print("=" * 70)
    print("WORLD MODEL PREDICTIONS")
    print("=" * 70)
    print(f"\nCurrent state:")
    print(f"  HP: 50/100 (50%)  |  Level: 5  |  XP: 1,200")
    print(f"  Position: (7, 12)")
    print(f"  Nearby: Rat (2 tiles), Guard (5 tiles)")
    print(f"  Weapon: Bronze Axe  |  Inventory: 3 items")
    print(f"  Quest: Kill Rats (stage 2/5)")

    actions = ["attack", "navigate", "eat", "equip", "interact_npc", "wait", "warp", "heal"]

    print(f"\n{'Action':<15} {'HP Δ':>8} {'XP Δ':>8} {'Death%':>8} {'Reward':>8}")
    print("-" * 55)

    for action in actions:
        action_vec = aenc.encode(action) 
        action_t = torch.from_numpy(action_vec).unsqueeze(0).to(device)

        with torch.no_grad():
            delta, reward, terminal = model(state_t, action_t)

        d = delta.cpu().numpy().squeeze()
        hp_delta = d[0] * 100  # hp_ratio * max_hp (approx)
        xp_delta = d[3] * enc.XP_MAX

        death_pct = terminal.cpu().item() * 100
        reward_val = reward.cpu().item()

        print(f"  {action:<13} {hp_delta:>+7.1f} {xp_delta:>+7.0f} {death_pct:>6.2f}% {reward_val:>+7.4f}")


def demo_mcts(model, device):
    """Run MCTS and show the decision-making process."""
    print("\n" + "=" * 70)
    print("MCTS PLANNING (200 simulations)")
    print("=" * 70)

    planner = KaetramMCTS(model, n_simulations=200, device=str(device))

    state = {
        "player_stats": {"hp": 50, "max_hp": 100, "level": 5, "experience": 1200},
        "player_position": {"x": 7, "y": 12},
        "nearby_entities": [
            {"name": "Rat", "type": "mob", "hp": 15, "max_hp": 15, "distance": 2, "aggressive": False},
        ],
        "equipment": {"weapon": "Bronze Axe"},
        "inventory": [{"name": "bread"}] * 3,
        "active_quests": [{"name": "Kill Rats", "stage": 2, "stageCount": 5}],
        "ui_state": {"is_dead": False},
    }

    print(f"\nScenario: HP at 50%, Rat nearby, Bronze Axe equipped, bread in bag")
    print(f"Thinking...", end=" ", flush=True)

    result = planner.search(state)

    print(f"done! ({result['simulations']} simulations)\n")

    print(f"{'Action':<15} {'Visits':>8} {'Value':>8} {'Death%':>8}")
    print("-" * 45)

    sorted_actions = sorted(result["action_scores"].items(), key=lambda x: -x[1]["visits"])
    for action, scores in sorted_actions:
        print(f"  {action:<13} {scores['visits']:>6d} {scores['value']:>+7.3f} {scores['terminal_prob']*100:>6.2f}%")

    print(f"\n  → Best action: \033[1;32m{result['best_action']}\033[0m")

    # Second scenario: low HP
    print(f"\n{'─' * 45}")
    state["player_stats"]["hp"] = 15
    print(f"\nScenario 2: HP critically low (15/100)...")
    print(f"Thinking...", end=" ", flush=True)
    result2 = planner.search(state)
    print(f"done!\n")

    sorted_actions = sorted(result2["action_scores"].items(), key=lambda x: -x[1]["visits"])
    for action, scores in sorted_actions:
        print(f"  {action:<13} {scores['visits']:>6d} {scores['value']:>+7.3f} {scores['terminal_prob']*100:>6.2f}%")
    print(f"\n  → Best action: \033[1;32m{result2['best_action']}\033[0m")


def main():
    parser = argparse.ArgumentParser(description="World Model Interactive Demo")
    parser.add_argument("--model", type=str, default="world/checkpoints/best.pt")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = KaetramWorldModel()
    ckpt = torch.load(args.model, map_location=device, weights_only=False)
    model.load_state_dict(ckpt["model_state_dict"])
    model.to(device)
    model.eval()
    print(f"Loaded model (epoch {ckpt['epoch']}, val_loss {ckpt['val_loss']:.4f})")

    demo_predictions(model, device)
    demo_mcts(model, device)

    print(f"\n{'=' * 70}")
    print("The model simulated hundreds of future game states in milliseconds.")
    print("This is how the agent can 'think ahead' before acting.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
