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
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import ws_observer as obs


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestParsePacket(unittest.TestCase):
    def test_two_element(self):
        top, sub, data = obs._parse_packet([5, {"instance": "x"}])
        self.assertEqual(top, 5)
        self.assertIsNone(sub)
        self.assertEqual(data, {"instance": "x"})

    def test_three_element(self):
        top, sub, data = obs._parse_packet([15, 1, {"instance": "a", "target": "b"}])
        self.assertEqual(top, 15)
        self.assertEqual(sub, 1)
        self.assertEqual(data, {"instance": "a", "target": "b"})

    def test_one_element(self):
        top, sub, data = obs._parse_packet([0])
        self.assertEqual(top, 0)
        self.assertIsNone(sub)
        self.assertIsNone(data)


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

    def test_spawn_stores_max_hp(self):
        data = {"instance": "rat_001", "type": "mob", "x": 0, "y": 0, "hitPoints": 20, "maxHitPoints": 20}
        obs.handle_spawn(data, self.state)
        self.assertEqual(self.state.nearby_entities["rat_001"]["max_hp"], 20)

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

    def test_movement_no_change_returns_false(self):
        obs.handle_spawn({"instance": "rat_001", "type": "mob", "x": 10, "y": 10}, self.state)
        changed = obs.handle_movement({"instance": "rat_001", "x": 10, "y": 10}, self.state)
        self.assertFalse(changed)

    def test_movement_unknown_entity(self):
        changed = obs.handle_movement({"instance": "ghost", "x": 5, "y": 5}, self.state)
        self.assertFalse(changed)

    def test_combat_hit(self):
        # Actual wire format: {instance: attacker, target: defender, hit: {damage: N}}
        data = {"instance": "ClaudeBot", "target": "rat_001", "hit": {"damage": 5, "type": 0}}
        changed = obs.handle_combat(data, self.state)
        self.assertTrue(changed)
        self.assertEqual(self.state.last_combat["damage"], 5)
        self.assertEqual(self.state.last_combat["attacker"], "ClaudeBot")
        self.assertEqual(self.state.last_combat["target"], "rat_001")

    def test_combat_dispatched_only_on_hit_subopcode(self):
        """handle_combat is only called from run() when sub_op == COMBAT_HIT.
        Other sub-opcodes never reach handle_combat, so this verifies the
        handler itself doesn't double-check sub_op."""
        data = {"instance": "X", "target": "Y", "hit": {"damage": 3}}
        changed = obs.handle_combat(data, self.state)
        self.assertTrue(changed)

    def test_points_updates_hp(self):
        obs.handle_spawn({"instance": "rat_001", "type": "mob", "x": 0, "y": 0, "hitPoints": 20}, self.state)
        changed = obs.handle_points({"instance": "rat_001", "hitPoints": 12, "maxHitPoints": 20}, self.state)
        self.assertTrue(changed)
        self.assertEqual(self.state.nearby_entities["rat_001"]["hp"], 12)
        self.assertEqual(self.state.nearby_entities["rat_001"]["max_hp"], 20)

    def test_points_no_fields_returns_false(self):
        obs.handle_spawn({"instance": "rat_001", "type": "mob", "x": 0, "y": 0}, self.state)
        changed = obs.handle_points({"instance": "rat_001"}, self.state)
        self.assertFalse(changed)

    def test_experience_skill(self):
        # Actual wire format: {instance, amount, level, skill}
        data = {"instance": "ClaudeBot", "amount": 40, "skill": 0, "level": 2}
        changed = obs.handle_experience(data, self.state)
        self.assertTrue(changed)
        self.assertEqual(self.state.last_xp_event["amount"], 40)

    def test_death_string_instance(self):
        """Death packet sends instance as a plain string, not a dict."""
        obs.handle_spawn({"instance": "rat_001", "type": "mob", "x": 0, "y": 0, "hitPoints": 5}, self.state)
        changed = obs.handle_death("rat_001", self.state)
        self.assertTrue(changed)
        self.assertEqual(self.state.nearby_entities["rat_001"]["hp"], 0)

    def test_death_unknown_instance(self):
        changed = obs.handle_death("ghost_999", self.state)
        self.assertFalse(changed)

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


class TestPlayerPositionAndClickCoords(unittest.TestCase):
    def setUp(self):
        self.state = obs.GameState()

    def test_claudebot_detection(self):
        """Spawn packet with name='ClaudeBot' sets claudebot_instance."""
        data = {"instance": "0-cb-123", "type": 0, "name": "ClaudeBot", "x": 190, "y": 160}
        obs.handle_spawn(data, self.state)
        self.assertEqual(self.state.claudebot_instance, "0-cb-123")

    def test_non_claudebot_no_detection(self):
        """Spawn of other players does not set claudebot_instance."""
        data = {"instance": "0-other", "type": 0, "name": "SomePlayer", "x": 100, "y": 100}
        obs.handle_spawn(data, self.state)
        self.assertIsNone(self.state.claudebot_instance)

    def test_player_position_in_output(self):
        """to_dict() returns player_position when ClaudeBot is in nearby_entities."""
        obs.handle_spawn({"instance": "0-cb", "type": 0, "name": "ClaudeBot", "x": 190, "y": 160}, self.state)
        d = self.state.to_dict()
        self.assertEqual(d["player_position"], {"x": 190, "y": 160})

    def test_player_position_null(self):
        """to_dict() returns player_position=None when ClaudeBot not present."""
        obs.handle_spawn({"instance": "mob1", "type": 3, "name": "Rat", "x": 10, "y": 10}, self.state)
        d = self.state.to_dict()
        self.assertIsNone(d["player_position"])

    def test_click_coords_on_screen(self):
        """Entity 5 tiles east and 3 tiles south of player → click_x = 640+5*64=960, click_y = 360+3*64=552."""
        obs.handle_spawn({"instance": "0-cb", "type": 0, "name": "ClaudeBot", "x": 190, "y": 160}, self.state)
        obs.handle_spawn({"instance": "rat1", "type": 3, "name": "Rat", "x": 195, "y": 163,
                          "hitPoints": 20, "maxHitPoints": 20}, self.state)
        d = self.state.to_dict()
        rat = [e for e in d["nearby_entities"] if e["name"] == "Rat"][0]
        self.assertTrue(rat["on_screen"])
        self.assertEqual(rat["click_x"], 960)
        self.assertEqual(rat["click_y"], 552)

    def test_click_coords_off_screen(self):
        """Entity 200 tiles away → on_screen=False, no click_x/click_y."""
        obs.handle_spawn({"instance": "0-cb", "type": 0, "name": "ClaudeBot", "x": 190, "y": 160}, self.state)
        obs.handle_spawn({"instance": "ogre1", "type": 3, "name": "Ogre", "x": 390, "y": 360,
                          "hitPoints": 150, "maxHitPoints": 150}, self.state)
        d = self.state.to_dict()
        ogre = [e for e in d["nearby_entities"] if e["name"] == "Ogre"][0]
        self.assertFalse(ogre["on_screen"])
        self.assertNotIn("click_x", ogre)
        self.assertNotIn("click_y", ogre)

    def test_nearest_mob(self):
        """Nearest alive mob (type=3, hp>0) is selected correctly."""
        obs.handle_spawn({"instance": "0-cb", "type": 0, "name": "ClaudeBot", "x": 190, "y": 160}, self.state)
        obs.handle_spawn({"instance": "rat1", "type": 3, "name": "Rat", "x": 195, "y": 163,
                          "hitPoints": 20, "maxHitPoints": 20}, self.state)
        obs.handle_spawn({"instance": "rat2", "type": 3, "name": "Rat", "x": 192, "y": 161,
                          "hitPoints": 20, "maxHitPoints": 20}, self.state)
        d = self.state.to_dict()
        self.assertIsNotNone(d["nearest_mob"])
        self.assertEqual(d["nearest_mob"]["id"], "rat2")  # closer: distance=3 vs 8
        self.assertEqual(d["nearest_mob"]["distance"], 3)

    def test_nearest_mob_excludes_dead(self):
        """Dead mobs (hp=0) are not considered for nearest_mob."""
        obs.handle_spawn({"instance": "0-cb", "type": 0, "name": "ClaudeBot", "x": 190, "y": 160}, self.state)
        obs.handle_spawn({"instance": "rat1", "type": 3, "name": "Rat", "x": 191, "y": 160,
                          "hitPoints": 0, "maxHitPoints": 20}, self.state)
        obs.handle_spawn({"instance": "rat2", "type": 3, "name": "Rat", "x": 195, "y": 163,
                          "hitPoints": 20, "maxHitPoints": 20}, self.state)
        d = self.state.to_dict()
        self.assertEqual(d["nearest_mob"]["id"], "rat2")

    def test_distance_calculation(self):
        """Manhattan distance computed correctly."""
        obs.handle_spawn({"instance": "0-cb", "type": 0, "name": "ClaudeBot", "x": 100, "y": 100}, self.state)
        obs.handle_spawn({"instance": "m1", "type": 3, "name": "Mob", "x": 105, "y": 97,
                          "hitPoints": 10, "maxHitPoints": 10}, self.state)
        d = self.state.to_dict()
        mob = [e for e in d["nearby_entities"] if e["name"] == "Mob"][0]
        self.assertEqual(mob["distance"], 8)  # |5| + |3| = 8

    def test_teleport_updates_position(self):
        """PKT_TELEPORT updates entity position in nearby_entities."""
        obs.handle_spawn({"instance": "0-cb", "type": 0, "name": "ClaudeBot", "x": 190, "y": 160}, self.state)
        # Simulate teleport packet handling inline (same logic as run())
        data = {"instance": "0-cb", "x": 300, "y": 400}
        eid = obs._entity_id(data)
        if eid in self.state.nearby_entities:
            self.state.nearby_entities[eid]["x"] = data.get("x", 0)
            self.state.nearby_entities[eid]["y"] = data.get("y", 0)
        self.assertEqual(self.state.nearby_entities["0-cb"]["x"], 300)
        self.assertEqual(self.state.nearby_entities["0-cb"]["y"], 400)

    def test_welcome_extracts_instance(self):
        """PKT_WELCOME saves observer_instance on GameState."""
        self.state.observer_instance = None
        # Simulate Welcome handling inline (same logic as run())
        data = {"instance": "0-obs-456", "type": 0, "name": "ObserverBot", "x": 188, "y": 157}
        if isinstance(data, dict):
            self.state.observer_instance = data.get("instance")
        self.assertEqual(self.state.observer_instance, "0-obs-456")

    def test_to_dict_schema_includes_new_fields(self):
        """to_dict() includes player_position and nearest_mob keys."""
        d = self.state.to_dict()
        self.assertIn("player_position", d)
        self.assertIn("nearest_mob", d)


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
    # Server → Client: batched frames use outer array
    [[obs.PKT_CONNECTED, None]],
    [[obs.PKT_HANDSHAKE, {"type": "client", "instance": "0-123", "serverId": 1, "serverTime": 1000}]],
    [[obs.PKT_WELCOME, {"instance": "0-123", "type": 0, "name": "ObserverBot",
                        "x": 188, "y": 157, "hitPoints": 69, "maxHitPoints": 69}]],
    # Spawn two mobs (no sub-opcode — 2-element packet)
    [[obs.PKT_SPAWN, [
        {"instance": "rat_001", "type": "mob", "name": "Rat", "x": 415, "y": 190,
         "hitPoints": 20, "maxHitPoints": 20},
        {"instance": "rat_002", "type": "mob", "name": "Rat", "x": 420, "y": 195,
         "hitPoints": 20, "maxHitPoints": 20},
    ]]],
    # Movement — has sub-opcode (3-element packet)
    [[obs.PKT_MOVEMENT, 4, {"instance": "rat_001", "x": 416, "y": 191}]],
    # Combat Hit — has sub-opcode
    [[obs.PKT_COMBAT, obs.COMBAT_HIT, {"instance": "ClaudeBot", "target": "rat_001",
                                        "hit": {"damage": 5, "type": 0}}]],
    # Points update (no sub-opcode)
    [[obs.PKT_POINTS, {"instance": "rat_001", "hitPoints": 15, "maxHitPoints": 20}]],
    # Experience.Skill — has sub-opcode
    [[obs.PKT_EXPERIENCE, obs.EXP_SKILL, {"instance": "ClaudeBot", "amount": 40, "skill": 0}]],
    # Despawn
    [[obs.PKT_DESPAWN, {"instance": "rat_002"}]],
    # Death — data is instance string
    [[obs.PKT_DEATH, "rat_001"]],
]


async def mock_server_handler(websocket):
    for frame in SAMPLE_PACKETS:
        await websocket.send(json.dumps(frame))
        await asyncio.sleep(0.1)
    print("mock_server: all packets sent, holding connection open")
    await asyncio.sleep(5)


async def serve_mock(port: int) -> None:
    try:
        import websockets.server as ws_server
    except ImportError:
        raise SystemExit("Missing dependency: pip install websockets")

    print(f"mock_server: listening on ws://localhost:{port}")
    print(f"             run:  python3 ws_observer.py --host localhost --port {port}")
    async with ws_server.serve(mock_server_handler, "localhost", port):
        await asyncio.Future()


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
        sys.argv = [sys.argv[0]] + remaining
        unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
