#!/usr/bin/env python3
"""
ws_observer.py — Connects to Kaetram's WebSocket as ObserverBot and writes
structured game state to state/game_state.json on every meaningful packet.

Run alongside play.sh + logger.py:
    python3 ws_observer.py [--host localhost] [--port 9001]
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
PKT_HANDSHAKE  = 1
PKT_LOGIN      = 2
PKT_WELCOME    = 3
PKT_SPAWN      = 5
PKT_SYNC       = 10
PKT_MOVEMENT   = 11
PKT_DESPAWN    = 13
PKT_COMBAT     = 15
PKT_POINTS     = 17
PKT_EXPERIENCE = 28
PKT_DEATH      = 29

# Combat sub-opcodes (opcodes.ts Combat enum)
COMBAT_HIT = 1

# Experience sub-opcodes (opcodes.ts Experience enum)
EXP_SKILL = 1


class GameState:
    def __init__(self):
        self.nearby_entities: dict[str, dict] = {}  # id → entity dict
        self.last_combat: dict | None = None
        self.last_xp_event: dict | None = None

    def to_dict(self) -> dict:
        return {
            "timestamp": time.time(),
            "nearby_entities": list(self.nearby_entities.values()),
            "last_combat": self.last_combat,
            "last_xp_event": self.last_xp_event,
            "player_count_nearby": sum(
                1 for e in self.nearby_entities.values() if e.get("type") == "player"
            ),
        }


def write_state(state: GameState) -> None:
    tmp = GAME_STATE_OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2))
    tmp.replace(GAME_STATE_OUT)


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
            "id":   eid,
            "type": ent.get("type", "unknown"),
            "name": ent.get("name", ""),
            "x":    ent.get("x", 0),
            "y":    ent.get("y", 0),
            "hp":   ent.get("hitPoints", ent.get("hp", 0)),
        }
        changed = True
    return changed


def handle_despawn(data: dict, state: GameState) -> bool:
    eid = str(data.get("instance") or data.get("id") or "")
    if eid and eid in state.nearby_entities:
        del state.nearby_entities[eid]
        return True
    return False


def handle_movement(data: dict, state: GameState) -> bool:
    eid = str(data.get("instance") or data.get("id") or "")
    if eid and eid in state.nearby_entities:
        state.nearby_entities[eid]["x"] = data.get("x", state.nearby_entities[eid]["x"])
        state.nearby_entities[eid]["y"] = data.get("y", state.nearby_entities[eid]["y"])
        return True
    return False


def handle_combat(data: dict, state: GameState) -> bool:
    # Only record Hit sub-opcode
    if data.get("opcode") != COMBAT_HIT:
        return False
    state.last_combat = {
        "attacker": data.get("attackerId", data.get("attacker", "")),
        "target":   data.get("targetId",   data.get("target", "")),
        "damage":   data.get("damage", 0),
    }
    return True


def handle_points(data: dict, state: GameState) -> bool:
    eid = str(data.get("instance") or data.get("id") or "")
    if eid and eid in state.nearby_entities:
        if "hitPoints" in data:
            state.nearby_entities[eid]["hp"] = data["hitPoints"]
        return True
    return False


def handle_experience(data: dict, state: GameState) -> bool:
    # Skill sub-opcode carries per-skill XP gain
    if data.get("opcode") == EXP_SKILL or "amount" in data or "experience" in data:
        state.last_xp_event = {
            "amount": data.get("amount", data.get("experience", 0)),
            "skill":  data.get("skill", "unknown"),
        }
        return True
    return False


def handle_death(data: dict, state: GameState) -> bool:
    eid = str(data.get("instance") or data.get("id") or "")
    if eid and eid in state.nearby_entities:
        state.nearby_entities[eid]["hp"] = 0
        return True
    return False


HANDLERS = {
    PKT_SPAWN:      handle_spawn,
    PKT_DESPAWN:    handle_despawn,
    PKT_MOVEMENT:   handle_movement,
    PKT_COMBAT:     handle_combat,
    PKT_POINTS:     handle_points,
    PKT_EXPERIENCE: handle_experience,
    PKT_DEATH:      handle_death,
}


async def run(host: str, port: int, username: str = "ObserverBot", password: str = "observer") -> None:
    uri = f"ws://{host}:{port}"
    state = GameState()
    print(f"ws_observer: connecting to {uri} as {username}")

    async with websockets.connect(uri) as ws:
        async for raw in ws:
            try:
                packet = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(packet, list) or len(packet) < 1:
                continue

            opcode = packet[0]
            data   = packet[1] if len(packet) > 1 else {}

            # Handshake → send login
            if opcode == PKT_HANDSHAKE:
                login_pkt = json.dumps([PKT_LOGIN, {"username": username, "password": password}])
                await ws.send(login_pkt)
                print(f"ws_observer: sent login as {username}")
                continue

            if opcode == PKT_WELCOME:
                print("ws_observer: logged in, observing…")
                continue

            handler = HANDLERS.get(opcode)
            if handler and isinstance(data, (dict, list)):
                changed = handler(data, state)
                if changed:
                    write_state(state)
                    print(
                        f"  [ws] opcode={opcode} entities={len(state.nearby_entities)}"
                        + (f" combat={state.last_combat}" if state.last_combat else "")
                    )


async def main_async(args: argparse.Namespace) -> None:
    GAME_STATE_OUT.parent.mkdir(exist_ok=True)
    backoff = 1
    while True:
        try:
            await run(args.host, args.port, args.username, args.password)
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
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
