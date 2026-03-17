#!/usr/bin/env python3
"""
ws_observer.py — Connects to Kaetram's WebSocket as ObserverBot and writes
structured game state to state/game_state.json on every meaningful packet.

Run alongside play.sh + logger.py:
    python3 ws_observer.py [--host localhost] [--port 9001]

Packet wire format (verified against Kaetram-Open source):
  Server → Client frames:  [[packetId, data], ...]          (batched, outer array)
  Packets with sub-opcode: [packetId, subOpcode, data]       (3 elements)
  Packets without:         [packetId, data]                  (2 elements)
  Client → Server:         [packetId, data]  or  [packetId, subOpcode, data]
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

try:
    import websockets
except ImportError:
    raise SystemExit("Missing dependency: pip install websockets")

BASE           = Path(__file__).parent
GAME_STATE_OUT = BASE / "state" / "game_state.json"

# ---------------------------------------------------------------------------
# Top-level Packets enum (packages/common/network/packets.ts, 0-indexed)
# ---------------------------------------------------------------------------
PKT_CONNECTED  = 0
PKT_HANDSHAKE  = 1
PKT_LOGIN      = 2
PKT_WELCOME    = 3
PKT_SPAWN      = 5
PKT_LIST       = 6   # Entity list — server sends IDs, client replies with Who
PKT_WHO        = 7   # Client requests spawn data for a list of entity IDs
PKT_READY      = 9
PKT_SYNC       = 10
PKT_MOVEMENT   = 11
PKT_DESPAWN    = 13
PKT_COMBAT     = 15
PKT_POINTS     = 17
PKT_EXPERIENCE = 28
PKT_DEATH      = 29

# Game version — must match server GVER (.env.defaults)
GVER = "0.5.5-beta"

# Login sub-opcodes (opcodes.ts Login enum): Login=0, Register=1, Guest=2
LOGIN_GUEST = 2

# Combat sub-opcodes (opcodes.ts Combat enum): Initiate=0, Hit=1, Finish=2, Sync=3
COMBAT_HIT = 1

# Experience sub-opcodes (opcodes.ts Experience enum): Sync=0, Skill=1
EXP_SKILL = 1


def _parse_packet(packet: list) -> tuple:
    """Returns (top_opcode, sub_opcode, data).

    Kaetram's Packet.serialize() produces:
      [packetId, data]             when opcode is undefined
      [packetId, subOpcode, data]  when opcode is present
    """
    if len(packet) == 3:
        return packet[0], packet[1], packet[2]
    data = packet[1] if len(packet) >= 2 else None
    return packet[0], None, data


class GameState:
    def __init__(self):
        self.nearby_entities: dict[str, dict] = {}  # id → entity dict
        self.last_combat: dict | None = None
        self.last_xp_event: dict | None = None

    def to_dict(self) -> dict:
        entities = list(self.nearby_entities.values())
        player_count = sum(1 for e in entities if e.get("type") == "player")
        return {
            "timestamp": time.time(),
            "nearby_entities": entities,
            "last_combat": self.last_combat,
            "last_xp_event": self.last_xp_event,
            "player_count_nearby": player_count,
        }


def write_state(state: GameState) -> None:
    tmp = GAME_STATE_OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_dict()))
    tmp.replace(GAME_STATE_OUT)


def _entity_id(data: dict) -> str:
    return str(data.get("instance") or data.get("id") or "")


def handle_spawn(data: dict | list, state: GameState) -> bool:
    """Spawn packet — one or more entities entering the area."""
    entities = data if isinstance(data, list) else [data]
    changed = False
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        eid = str(ent.get("instance") or ent.get("id") or "")
        if not eid:
            continue
        state.nearby_entities[eid] = {
            "id":     eid,
            "type":   ent.get("type", "unknown"),
            "name":   ent.get("name", ""),
            "x":      ent.get("x", 0),
            "y":      ent.get("y", 0),
            "hp":     ent.get("hitPoints", ent.get("hp", 0)),
            "max_hp": ent.get("maxHitPoints", 0),
        }
        changed = True
    return changed


def handle_despawn(data: dict, state: GameState) -> bool:
    eid = _entity_id(data)
    if eid and eid in state.nearby_entities:
        del state.nearby_entities[eid]
        return True
    return False


def handle_movement(data: dict, state: GameState) -> bool:
    """Movement packet — only update position if x/y actually changed."""
    if not isinstance(data, dict):
        return False
    eid = _entity_id(data)
    if not eid or eid not in state.nearby_entities:
        return False
    ent = state.nearby_entities[eid]
    new_x = data.get("x", ent["x"])
    new_y = data.get("y", ent["y"])
    if new_x == ent["x"] and new_y == ent["y"]:
        return False
    ent["x"], ent["y"] = new_x, new_y
    return True


def handle_combat(data: dict, state: GameState) -> bool:
    """Combat.Hit packet — attacker=instance, defender=target, damage=hit.damage."""
    if not isinstance(data, dict):
        return False
    hit = data.get("hit", {}) or {}
    state.last_combat = {
        "attacker": data.get("instance", ""),
        "target":   data.get("target", ""),
        "damage":   hit.get("damage", 0),
    }
    return True


def handle_points(data: dict, state: GameState) -> bool:
    """Points packet — updates hp/max_hp/mana/max_mana for a known entity."""
    if not isinstance(data, dict):
        return False
    eid = _entity_id(data)
    if not eid or eid not in state.nearby_entities:
        return False
    ent = state.nearby_entities[eid]
    changed = False
    if "hitPoints" in data:
        ent["hp"] = data["hitPoints"]
        changed = True
    if "maxHitPoints" in data:
        ent["max_hp"] = data["maxHitPoints"]
        changed = True
    if "mana" in data:
        ent["mana"] = data["mana"]
        changed = True
    if "maxMana" in data:
        ent["max_mana"] = data["maxMana"]
        changed = True
    return changed


def handle_experience(data: dict, state: GameState) -> bool:
    """Experience.Skill packet — {instance, amount, level, skill}."""
    if not isinstance(data, dict):
        return False
    state.last_xp_event = {
        "amount": data.get("amount", 0),
        "skill":  data.get("skill", "unknown"),
        "level":  data.get("level"),
    }
    return True


def handle_death(data: str | dict, state: GameState) -> bool:
    """Death packet — data is the instance string directly (not a dict)."""
    # Server sends: DeathPacket(instance) → [29, instance_string]
    eid = data if isinstance(data, str) else _entity_id(data)
    if eid and eid in state.nearby_entities:
        state.nearby_entities[eid]["hp"] = 0
        return True
    return False


async def run(host: str, port: int, username: str = "ObserverBot", password: str = "observer", debug: bool = False) -> None:
    uri = f"ws://{host}:{port}"
    state = GameState()
    print(f"ws_observer: connecting to {uri} as {username}")

    async with websockets.connect(uri) as ws:
        async for raw in ws:
            if debug:
                print(f"  [raw] {raw[:500]}")

            try:
                # Server batches packets: [[packetId, ...], [packetId, ...], ...]
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(frame, list) or not frame:
                continue

            # Outer array: each element is one packet
            packets = frame if isinstance(frame[0], list) else [frame]

            for packet in packets:
                if not isinstance(packet, list) or len(packet) < 1:
                    continue

                top_op, sub_op, data = _parse_packet(packet)

                # ---------- handshake flow ----------
                if top_op == PKT_CONNECTED:
                    await ws.send(json.dumps([PKT_HANDSHAKE, {"gVer": GVER}]))
                    if debug:
                        print(f"  >> sent Handshake gVer={GVER}")
                    continue

                if top_op == PKT_HANDSHAKE:
                    await ws.send(json.dumps([PKT_LOGIN, {"opcode": LOGIN_GUEST}]))
                    print(f"ws_observer: sent guest login")
                    continue

                if top_op == PKT_WELCOME:
                    print("ws_observer: logged in, observing…")
                    # Tell server we're ready — triggers updateRegion/updateEntities
                    await ws.send(json.dumps([PKT_READY, {"regionsLoaded": 0, "userAgent": "ws_observer"}]))
                    continue

                # Entity list: server sends IDs of nearby entities.
                # Reply with Who to request their spawn data.
                if top_op == PKT_LIST and isinstance(data, dict):
                    entity_ids = data.get("entities", [])
                    if entity_ids:
                        await ws.send(json.dumps([PKT_WHO, entity_ids]))
                        if debug:
                            print(f"  >> Who({len(entity_ids)} entities)")
                    continue

                # ---------- state packet handlers ----------
                changed = False

                if top_op == PKT_SPAWN and isinstance(data, (dict, list)):
                    changed = handle_spawn(data, state)

                elif top_op == PKT_DESPAWN and isinstance(data, dict):
                    changed = handle_despawn(data, state)

                elif top_op == PKT_MOVEMENT and isinstance(data, dict):
                    changed = handle_movement(data, state)

                elif top_op == PKT_COMBAT and sub_op == COMBAT_HIT and isinstance(data, dict):
                    changed = handle_combat(data, state)

                elif top_op == PKT_POINTS and isinstance(data, dict):
                    changed = handle_points(data, state)

                elif top_op == PKT_EXPERIENCE and sub_op == EXP_SKILL and isinstance(data, dict):
                    changed = handle_experience(data, state)

                elif top_op == PKT_DEATH:
                    changed = handle_death(data, state)

                elif debug:
                    print(f"  [ws] op={top_op} sub={sub_op} data={json.dumps(data)[:150] if data is not None else None}")

                if changed:
                    write_state(state)
                    print(
                        f"  [ws] op={top_op} entities={len(state.nearby_entities)}"
                        + (f" combat={state.last_combat}" if state.last_combat else "")
                    )


async def main_async(args: argparse.Namespace) -> None:
    GAME_STATE_OUT.parent.mkdir(exist_ok=True)
    backoff = 1
    while True:
        try:
            await run(args.host, args.port, args.username, args.password, debug=args.debug)
        except (OSError, websockets.exceptions.WebSocketException) as e:
            print(f"ws_observer: connection error — {e}; retrying in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except KeyboardInterrupt:
            print("\nws_observer: stopped.")
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Kaetram WebSocket observer")
    parser.add_argument("--host",     default="localhost")
    parser.add_argument("--port",     type=int, default=9001)
    parser.add_argument("--username", default="ObserverBot")
    parser.add_argument("--password", default="observer")
    parser.add_argument("--debug",    action="store_true", help="Print every raw packet")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
