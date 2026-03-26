#!/usr/bin/env python3
"""
mcts.py — Monte Carlo Tree Search planner using the trained Kaetram World Model.

Given a current game state, searches over possible actions using the world model
to simulate outcomes, returning the best action.

Usage (standalone test):
    python -m world.mcts --model world/checkpoints/best.pt
"""

import math
import argparse
from typing import Optional

import numpy as np
import torch

from world.schema import (
    StateEncoder, ActionEncoder, ACTION_TYPES, ACTION_TO_IDX,
    STATE_DIM, ACTION_DIM, NUM_ACTIONS,
)
from world.model import KaetramWorldModel


class MCTSNode:
    """A node in the MCTS search tree."""

    __slots__ = ["state_vec", "action", "parent", "children",
                 "visit_count", "value_sum", "reward", "terminal_prob"]

    def __init__(self, state_vec: np.ndarray, action: str = "", parent=None):
        self.state_vec = state_vec
        self.action = action
        self.parent = parent
        self.children: list["MCTSNode"] = []
        self.visit_count = 0
        self.value_sum = 0.0
        self.reward = 0.0
        self.terminal_prob = 0.0

    @property
    def value(self) -> float:
        if self.visit_count == 0:
            return 0.0
        return self.value_sum / self.visit_count

    def ucb_score(self, c_puct: float = 1.4) -> float:
        """Upper Confidence Bound for tree policy."""
        if self.visit_count == 0:
            return float("inf")
        parent_visits = self.parent.visit_count if self.parent else 1
        exploration = c_puct * math.sqrt(math.log(parent_visits) / self.visit_count)
        return self.value + exploration

    def is_leaf(self) -> bool:
        return len(self.children) == 0


class KaetramMCTS:
    """
    MCTS planner that uses the trained world model to simulate game outcomes.

    Given a game state, it:
    1. Enumerates legal actions
    2. Simulates each with the world model
    3. Scores trajectories (XP gain, survival, efficiency)
    4. Returns the best action
    """

    def __init__(
        self,
        world_model: KaetramWorldModel,
        n_simulations: int = 200,
        c_puct: float = 1.4,
        max_depth: int = 8,
        device: str = "auto",
    ):
        self.model = world_model
        self.n_simulations = n_simulations
        self.c_puct = c_puct
        self.max_depth = max_depth
        self.state_enc = StateEncoder()
        self.action_enc = ActionEncoder()

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self.model.to(self.device)
        self.model.eval()

    def get_legal_actions(self, state_vec: np.ndarray) -> list[str]:
        """Determine which actions are legal given the current state."""
        actions = []

        hp_ratio = state_vec[0]  # normalized HP
        is_dead = state_vec[13] > 0.5  # is_dead flag

        if is_dead:
            return ["respawn"]

        # Always available
        actions.extend(["attack", "navigate", "wait"])

        # Heal if HP < 70%
        if hp_ratio < 0.7:
            actions.append("eat")
            actions.append("heal")

        # Context-dependent
        actions.extend(["interact_npc", "equip", "click_entity", "warp"])

        return actions

    def simulate_action(
        self, state_vec: np.ndarray, action: str
    ) -> tuple[np.ndarray, float, float]:
        """Use world model to predict the next state from (state, action).

        Returns: (next_state_vec, reward, terminal_prob)
        """
        action_vec = self.action_enc.encode(action)

        state_t = torch.from_numpy(state_vec).unsqueeze(0).to(self.device)
        action_t = torch.from_numpy(action_vec).unsqueeze(0).to(self.device)

        with torch.no_grad():
            delta, reward, terminal = self.model(state_t, action_t)

        next_state = state_vec + delta.cpu().numpy().squeeze()
        # Clamp to valid range
        next_state = np.clip(next_state, 0.0, 1.0)

        return (
            next_state,
            float(reward.cpu().item()),
            float(terminal.cpu().item()),
        )

    def evaluate_leaf(self, node: MCTSNode) -> float:
        """Score a leaf node. Higher = better."""
        score = 0.0

        # Reward from XP gain
        score += node.reward * 10.0

        # Survival bonus
        score += (1.0 - node.terminal_prob) * 0.5

        # HP preservation
        hp_ratio = node.state_vec[0]
        score += hp_ratio * 0.3

        # Penalty for death
        if node.terminal_prob > 0.5:
            score -= 2.0

        # Small step penalty (prefer shorter paths)
        depth = 0
        n = node
        while n.parent:
            depth += 1
            n = n.parent
        score -= depth * 0.02

        return score

    def search(self, game_state: dict) -> dict:
        """Run MCTS search and return the best action with scores.

        Args:
            game_state: Raw JSON game state dict

        Returns:
            {
                "best_action": str,
                "action_scores": {action: {"visits": N, "value": V}},
                "simulations": int,
            }
        """
        state_vec = self.state_enc.encode(game_state)
        root = MCTSNode(state_vec)

        for _ in range(self.n_simulations):
            node = root

            # ── Selection: walk down the tree ──
            depth = 0
            while not node.is_leaf() and depth < self.max_depth:
                node = max(node.children, key=lambda c: c.ucb_score(self.c_puct))
                depth += 1

            # ── Expansion: if not terminal, expand all legal actions ──
            if node.terminal_prob < 0.9 and depth < self.max_depth:
                legal_actions = self.get_legal_actions(node.state_vec)
                for action in legal_actions:
                    next_state, reward, terminal = self.simulate_action(
                        node.state_vec, action
                    )
                    child = MCTSNode(next_state, action=action, parent=node)
                    child.reward = reward
                    child.terminal_prob = terminal
                    node.children.append(child)

                # Pick a random child for rollout
                if node.children:
                    node = node.children[np.random.randint(len(node.children))]

            # ── Evaluation ──
            value = self.evaluate_leaf(node)

            # ── Backpropagation ──
            while node is not None:
                node.visit_count += 1
                node.value_sum += value
                node = node.parent

        # ── Extract results ──
        action_scores = {}
        for child in root.children:
            action_scores[child.action] = {
                "visits": child.visit_count,
                "value": child.value,
                "reward": child.reward,
                "terminal_prob": child.terminal_prob,
            }

        # Best action = most visited
        if root.children:
            best_child = max(root.children, key=lambda c: c.visit_count)
            best_action = best_child.action
        else:
            best_action = "wait"

        return {
            "best_action": best_action,
            "action_scores": action_scores,
            "simulations": self.n_simulations,
        }


def main():
    """Smoke test with a random or loaded model."""
    parser = argparse.ArgumentParser(description="MCTS Planner smoke test")
    parser.add_argument("--model", type=str, default="",
                        help="Path to trained checkpoint (empty = random model)")
    parser.add_argument("--simulations", type=int, default=50)
    args = parser.parse_args()

    # Load or create model
    model = KaetramWorldModel()
    if args.model:
        ckpt = torch.load(args.model, map_location="cpu", weights_only=False)
        model.load_state_dict(ckpt["model_state_dict"])
        print(f"Loaded model from {args.model}")
    else:
        print("Using randomly initialized model (smoke test)")

    planner = KaetramMCTS(model, n_simulations=args.simulations)

    # Fake game state
    fake_state = {
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

    print(f"\nRunning MCTS with {args.simulations} simulations...")
    result = planner.search(fake_state)

    print(f"\nBest action: {result['best_action']}")
    print(f"\nAction scores:")
    for action, scores in sorted(result["action_scores"].items(),
                                   key=lambda x: -x[1]["visits"]):
        print(f"  {action:20s} | visits={scores['visits']:4d} | "
              f"value={scores['value']:+.3f} | "
              f"terminal={scores['terminal_prob']:.3f}")


if __name__ == "__main__":
    main()
