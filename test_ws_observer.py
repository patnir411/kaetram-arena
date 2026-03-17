#!/usr/bin/env python3
"""
test_ws_observer.py — Tests for ws_observer.py.

Two modes:

  # Unit tests (no live server needed):
  python3 test_ws_observer.py

  # Integration test — spin up mock server, then in another terminal run:
  #   python3 ws_observer.py --host localhost --port 9999
  python3 test_ws_observer.py --serve [--port 9999]
"""

import argparse
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))
import ws_observer as obs

# Opcode constants (mirrors ws_observer.py)
PKT_HANDSHAKE  = obs.PKT_HANDSHAKE
PKT_LOGIN      = obs.PKT_LOGIN
PKT_WELCOME    = obs.PKT_WELCOME
PKT_SPAWN      = obs.PKT_SPAWN
PKT_DESPAWN    = obs.PKT_DESPAWN
PKT_MOVEMENT   = obs.PKT_MOVEMENT
PKT_COMBAT     = obs.PKT_COMBAT
PKT_POINTS     = obs.PKT_POINTS
PKT_EXPERIENCE = obs.PKT_EXPERIENCE
PKT_DEATH      = obs.PKT_DEATH


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestGameState(unittest.TestCase):
    def setUp(self):
        self.state = obs.GameState()

    def test_spawn_single(self):
        data = {"instance": "rat_001", "type": "mob", "name": "Rat", "x": 415, "y": 190, "hitPoints": 20}
        changed = obs.handle_spawn(data, self.state)
        self.assertTrue(changed)
        self.assertIn("rat_001", self.state.nearby_entities)
        ent = self.state.nearby_entities["rat_001"]
        self.assertEqual(ent["x"], 415)
        self.assertEqual(ent["hp"], 20)

    def test_spawn_list(self):
        data = [
            {"instance": "mob_1", "type": "mob", "name": "Rat",   "x": 10, "y": 20, "hitPoints": 10},
            {"instance": "mob_2", "type": "mob", "name": "Snake", "x": 30, "y": 40, "hitPoints": 15},
        ]
        changed = obs.handle_spawn(data, self.state)
        self.assertTrue(changed)
        self.assertEqual(len(self.state.nearby_entities), 2)

    def test_despawn(self):
        obs.handle_spawn({"instance": "rat_001", "type": "mob", "x": 0, "y": 0}, self.state)
        changed = obs.handle_despawn({"instance": "rat_001"}, self.state)
        self.assertTrue(changed)
        self.assertNotIn("rat_001", self.state.nearby_entities)

    def test_despawn_unknown(self):
        changed = obs.handle_despawn({"instance": "nonexistent"}, self.state)
        self.assertFalse(changed)

    def test_movement_updates_position(self):
        obs.handle_spawn({"instance": "rat_001", "type": "mob", "x": 10, "y": 10}, self.state)
        changed = obs.handle_movement({"instance": "rat_001", "x": 20, "y": 25}, self.state)
        self.assertTrue(changed)
        self.assertEqual(self.state.nearby_entities["rat_001"]["x"], 20)
        self.assertEqual(self.state.nearby_entities["rat_001"]["y"], 25)

    def test_movement_unknown_entity(self):
        changed = obs.handle_movement({"instance": "ghost", "x": 5, "y": 5}, self.state)
        self.assertFalse(changed)

    def test_combat_hit(self):
        data = {"opcode": obs.COMBAT_HIT, "attackerId": "ClaudeBot", "targetId": "Rat", "damage": 5}
        changed = obs.handle_combat(data, self.state)
        self.assertTrue(changed)
        self.assertEqual(self.state.last_combat["damage"], 5)
        self.assertEqual(self.state.last_combat["attacker"], "ClaudeBot")

    def test_combat_non_hit_ignored(self):
        data = {"opcode": 0, "attackerId": "ClaudeBot", "targetId": "Rat"}  # Initiate
        changed = obs.handle_combat(data, self.state)
        self.assertFalse(changed)
        self.assertIsNone(self.state.last_combat)

    def test_points_updates_hp(self):
        obs.handle_spawn({"instance": "rat_001", "type": "mob", "x": 0, "y": 0, "hitPoints": 20}, self.state)
        changed = obs.handle_points({"instance": "rat_001", "hitPoints": 12}, self.state)
        self.assertTrue(changed)
        self.assertEqual(self.state.nearby_entities["rat_001"]["hp"], 12)

    def test_experience_skill(self):
        data = {"opcode": obs.EXP_SKILL, "amount": 40, "skill": "strength"}
        changed = obs.handle_experience(data, self.state)
        self.assertTrue(changed)
        self.assertEqual(self.state.last_xp_event["amount"], 40)
        self.assertEqual(self.state.last_xp_event["skill"], "strength")

    def test_experience_bare_amount(self):
        """Packets without opcode field but with 'amount' key should still register."""
        data = {"amount": 25, "skill": "magic"}
        changed = obs.handle_experience(data, self.state)
        self.assertTrue(changed)
        self.assertEqual(self.state.last_xp_event["amount"], 25)

    def test_death_zeroes_hp(self):
        obs.handle_spawn({"instance": "rat_001", "type": "mob", "x": 0, "y": 0, "hitPoints": 5}, self.state)
        changed = obs.handle_death({"instance": "rat_001"}, self.state)
        self.assertTrue(changed)
        self.assertEqual(self.state.nearby_entities["rat_001"]["hp"], 0)

    def test_player_count_nearby(self):
        obs.handle_spawn({"instance": "p1", "type": "player", "x": 0, "y": 0}, self.state)
        obs.handle_spawn({"instance": "m1", "type": "mob",    "x": 1, "y": 1}, self.state)
        d = self.state.to_dict()
        self.assertEqual(d["player_count_nearby"], 1)
        self.assertEqual(len(d["nearby_entities"]), 2)

    def test_to_dict_schema(self):
        d = self.state.to_dict()
        self.assertIn("timestamp", d)
        self.assertIn("nearby_entities", d)
        self.assertIn("last_combat", d)
        self.assertIn("last_xp_event", d)
        self.assertIn("player_count_nearby", d)


class TestWriteState(unittest.TestCase):
    def test_atomic_write(self):
        state = obs.GameState()
        obs.handle_spawn({"instance": "x", "type": "mob", "x": 1, "y": 2, "hitPoints": 10}, state)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "game_state.json"
            with patch.object(obs, "GAME_STATE_OUT", out):
                obs.write_state(state)
            self.assertTrue(out.exists())
            data = json.loads(out.read_text())
            self.assertEqual(len(data["nearby_entities"]), 1)
            self.assertEqual(data["nearby_entities"][0]["hp"], 10)


# ---------------------------------------------------------------------------
# Mock WebSocket server for integration testing
# ---------------------------------------------------------------------------

SAMPLE_PACKETS = [
    # Handshake
    [PKT_HANDSHAKE, {"version": "1.0.0"}],
    # Welcome (after login)
    [PKT_WELCOME, {"username": "ObserverBot"}],
    # Spawn two mobs
    [PKT_SPAWN, [
        {"instance": "rat_001", "type": "mob", "name": "Rat",   "x": 415, "y": 190, "hitPoints": 20},
        {"instance": "rat_002", "type": "mob", "name": "Rat",   "x": 420, "y": 195, "hitPoints": 20},
    ]],
    # Movement
    [PKT_MOVEMENT, {"instance": "rat_001", "x": 416, "y": 191}],
    # Combat hit
    [PKT_COMBAT, {"opcode": obs.COMBAT_HIT, "attackerId": "ClaudeBot", "targetId": "rat_001", "damage": 5}],
    # Points update
    [PKT_POINTS, {"instance": "rat_001", "hitPoints": 15}],
    # Experience
    [PKT_EXPERIENCE, {"opcode": obs.EXP_SKILL, "amount": 40, "skill": "strength"}],
    # Despawn
    [PKT_DESPAWN, {"instance": "rat_002"}],
    # Death
    [PKT_DEATH, {"instance": "rat_001"}],
]


async def mock_server_handler(websocket):
    """Send sample packets, wait for login, then continue."""
    for pkt in SAMPLE_PACKETS:
        await websocket.send(json.dumps(pkt))
        await asyncio.sleep(0.1)
    print("mock_server: all packets sent, holding connection open")
    # Hold open so the observer can finish writing
    await asyncio.sleep(5)


async def serve_mock(port: int) -> None:
    try:
        import websockets.server as ws_server
    except ImportError:
        raise SystemExit("Missing dependency: pip install websockets")

    print(f"mock_server: listening on ws://localhost:{port}")
    print(f"             run:  python3 ws_observer.py --host localhost --port {port}")
    async with ws_server.serve(mock_server_handler, "localhost", port):
        await asyncio.Future()  # run forever


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", help="Run mock WebSocket server")
    parser.add_argument("--port",  type=int, default=9999)
    args, remaining = parser.parse_known_args()

    if args.serve:
        asyncio.run(serve_mock(args.port))
    else:
        # Run unit tests, passing remaining args to unittest
        sys.argv = [sys.argv[0]] + remaining
        unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
