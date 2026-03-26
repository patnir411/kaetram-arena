#!/usr/bin/env python3
"""
schema.py — Lean state vectorization for the Kaetram World Model.

Only the essential combat-relevant fields. Everything else (equipment hashes,
quest hashes, position, padding) is dropped — those are static or better
served by lookup tables.

State vector (16 dims):
  [0]  hp_ratio           — current HP / max HP
  [1]  max_hp_norm        — max HP / 1000
  [2]  level_norm         — level / 100
  [3]  xp_norm            — experience / 100k
  [4]  ent0_hp            — nearest entity HP ratio
  [5]  ent0_dist          — nearest entity distance (normalized)
  [6]  ent0_aggro         — nearest entity is aggressive
  [7]  ent1_hp            — 2nd nearest HP ratio
  [8]  ent1_dist          — 2nd nearest distance
  [9]  ent1_aggro         — 2nd nearest aggressive
  [10] ent2_hp            — 3rd nearest HP ratio
  [11] ent2_dist          — 3rd nearest distance
  [12] ent2_aggro         — 3rd nearest aggressive
  [13] is_dead            — terminal flag
  [14] is_poisoned        — DOT damage flag
  [15] has_target         — currently in combat
"""

import numpy as np
from typing import Optional

# ── Canonical action set ──────────────────────────────────────────────────────
ACTION_TYPES = [
    "attack", "navigate", "eat", "equip", "interact_npc", "loot",
    "warp", "wait", "click", "click_entity", "click_tile", "move",
    "talk_npc", "respawn", "quest_accept", "heal", "set_style",
    "reconnect", "stuck_reset", "clear_combat", "nav_cancel", "other",
]
ACTION_TO_IDX = {a: i for i, a in enumerate(ACTION_TYPES)}
NUM_ACTIONS = len(ACTION_TYPES)

# ── Vector dimensions ────────────────────────────────────────────────────────
MAX_ENTITIES = 3      # top-3 nearest (combat-relevant)
STATE_DIM = 16        # 4 player stats + 3×3 entities + 3 flags
ACTION_DIM = NUM_ACTIONS + 4  # one-hot + (target_idx, arg_x, arg_y, arg_slot)


class StateEncoder:
    """Encode/decode JSON game states to lean 16-dim combat vectors."""

    dim = STATE_DIM

    HP_MAX = 1000.0
    XP_MAX = 100000.0
    LEVEL_MAX = 100.0
    POS_MAX = 500.0
    DIST_MAX = 20.0

    @staticmethod
    def _num(val, default=0):
        """Safely coerce to float."""
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return float(val)
            except ValueError:
                return float(default)
        return float(default)

    def encode(self, state: dict) -> np.ndarray:
        """Convert game state to float32 vector of shape (16,)."""
        vec = np.zeros(STATE_DIM, dtype=np.float32)
        n = self._num

        # ── Player stats (4 dims) ──
        ps = state.get("player_stats", state)
        if not isinstance(ps, dict):
            ps = state
        max_hp = max(n(ps.get("max_hp", ps.get("maxHp", 100)), 100), 1)
        vec[0] = n(ps.get("hp", ps.get("hitpoints", 0))) / max_hp
        vec[1] = max_hp / self.HP_MAX
        vec[2] = n(ps.get("level", 1), 1) / self.LEVEL_MAX
        vec[3] = n(ps.get("experience", ps.get("exp", 0))) / self.XP_MAX

        # ── Top-3 nearest entities (3×3 = 9 dims) ──
        entities = state.get("nearby_entities", state.get("entities", []))
        if isinstance(entities, list):
            sorted_ents = sorted(
                [e for e in entities if isinstance(e, dict)],
                key=lambda e: n(e.get("distance", 999), 999)
            )[:MAX_ENTITIES]
            for i, ent in enumerate(sorted_ents):
                base = 4 + i * 3
                ent_hp = n(ent.get("hp", ent.get("hitpoints", 0)))
                ent_max = n(ent.get("max_hp", ent.get("maxHitpoints", max(ent_hp, 1))), max(ent_hp, 1))
                vec[base] = ent_hp / max(ent_max, 1)
                vec[base + 1] = min(n(ent.get("distance", 20), 20), self.DIST_MAX) / self.DIST_MAX
                vec[base + 2] = 1.0 if ent.get("is_aggressive", ent.get("aggressive", False)) else 0.0

        # ── Combat flags (3 dims) ──
        ui = state.get("ui_state", {})
        if not isinstance(ui, dict):
            ui = {}
        vec[13] = 1.0 if ui.get("is_dead", state.get("is_dead", False)) else 0.0
        vec[14] = 1.0 if ui.get("is_poisoned", state.get("poisoned", False)) else 0.0
        vec[15] = 1.0 if ui.get("has_target", state.get("has_target", False)) else 0.0

        return vec

    def decode(self, vec: np.ndarray) -> dict:
        """Decode state vector to readable dict."""
        return {
            "hp_ratio": float(vec[0]),
            "max_hp": float(vec[1] * self.HP_MAX),
            "level": float(vec[2] * self.LEVEL_MAX),
            "xp": float(vec[3] * self.XP_MAX),
            "ent0_hp": float(vec[4]),
            "ent0_dist": float(vec[5] * self.DIST_MAX),
            "ent0_aggro": bool(vec[6] > 0.5),
            "is_dead": bool(vec[13] > 0.5),
            "is_poisoned": bool(vec[14] > 0.5),
            "has_target": bool(vec[15] > 0.5),
        }


class ActionEncoder:
    """Encode action type + args into a fixed-length vector."""

    dim = ACTION_DIM

    def encode(self, action_type: str, args: Optional[dict] = None) -> np.ndarray:
        vec = np.zeros(ACTION_DIM, dtype=np.float32)
        idx = ACTION_TO_IDX.get(action_type, ACTION_TO_IDX["other"])
        vec[idx] = 1.0
        if args:
            vec[NUM_ACTIONS] = args.get("target_idx", 0) / MAX_ENTITIES
            vec[NUM_ACTIONS + 1] = args.get("x", 0) / StateEncoder.POS_MAX
            vec[NUM_ACTIONS + 2] = args.get("y", 0) / StateEncoder.POS_MAX
            vec[NUM_ACTIONS + 3] = args.get("slot", 0) / 30.0
        return vec

    def decode(self, vec: np.ndarray) -> dict:
        one_hot = vec[:NUM_ACTIONS]
        action_idx = int(np.argmax(one_hot))
        return {
            "action": ACTION_TYPES[action_idx],
            "confidence": float(one_hot[action_idx]),
        }
