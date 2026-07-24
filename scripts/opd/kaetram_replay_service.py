"""Cold MCP and Mongo adapters used by the player-state reachability checker.

This module is intentionally independent of ``tests/``. Optional live-service
dependencies are imported lazily so offline checker invocation can fail closed
with a useful diagnostic before attempting any service connection.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
MCP_SERVER = REPO / "mcp_game_server.py"
STATE_EXTRACTOR = REPO / "state_extractor.js"
FIXED_BCRYPT_HASH = "$2a$10$C78OFhflOeBZOXhGo7XHQ.8d9FF5xAjRBrVjxDm.b6.WmgGLgghJG"
ALL_COLLECTIONS = (
    "player_info", "player_inventory", "player_bank", "player_equipment",
    "player_quests", "player_achievements", "player_skills",
    "player_statistics", "player_abilities",
)
NON_STACKABLE_KEYS = frozenset({
    "logs", "bluelily", "tomato", "strawberry", "paprika", "bowlsmall",
    "clamobject", "clamchowder", "string", "cd", "seaweedroll", "rawshrimp",
    "cookedshrimp", "nisocore", "coal", "tinore", "copperore", "bronzeore",
    "tinbar", "copperbar", "bronzebar", "bead", "icesword", "snowpotion",
    "apple", "stick",
})
TUTORIAL_FINISHED_QUEST = {
    "key": "tutorial", "stage": 16, "subStage": 0, "completedSubStages": [],
}
SKILL_NAME_TO_TYPE = {
    "lumberjacking": 0, "accuracy": 1, "archery": 2, "health": 3,
    "magic": 4, "mining": 5, "strength": 6, "defense": 7, "fishing": 8,
    "cooking": 9, "smithing": 10, "crafting": 11, "chiseling": 12,
    "fletching": 13, "loitering": 14, "foraging": 15, "eating": 16,
    "alchemy": 17, "smelting": 18,
}


def _inventory_slots(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots = [
        {"index": index, "key": "", "count": 0, "enchantments": {}}
        for index in range(25)
    ]
    pending: list[tuple[int, str, int, dict[str, Any]]] = []
    for item in items:
        index = int(item.get("index", 0))
        key = item.get("key", "")
        count = int(item.get("count", 0) or 0)
        enchantments = item.get("enchantments", {})
        if not key:
            continue
        if key in NON_STACKABLE_KEYS and count > 1:
            pending.extend((index + offset, key, 1, enchantments) for offset in range(count))
        else:
            pending.append((index, key, count, enchantments))
    used: set[int] = set()
    for index, key, count, enchantments in pending:
        while index < 25 and index in used:
            index += 1
        if index >= 25:
            raise RuntimeError("persistent player-state inventory exceeds 25 encoded slots")
        used.add(index)
        slots[index] = {
            "index": index, "key": key, "count": count, "enchantments": enchantments,
        }
    return slots


def _normalize_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for skill in skills:
        row = dict(skill)
        if "type" not in row:
            name = str(row.pop("name", "")).strip().lower()
            if name not in SKILL_NAME_TO_TYPE:
                raise RuntimeError(f"unknown skill name in persistent player state: {name!r}")
            row["type"] = SKILL_NAME_TO_TYPE[name]
        row.pop("level", None)
        row.pop("name", None)
        row["experience"] = int(row.get("experience", 0) or 0)
        normalized.append(row)
    return normalized


class PlayerStateStore:
    """Direct isolated Mongo store for complete candidate player snapshots."""

    def __init__(self, *, uri: str, database: str) -> None:
        self.uri = uri
        self.database = database

    def _client(self):
        from pymongo import MongoClient

        return MongoClient(self.uri, serverSelectionTimeoutMS=3000)

    @staticmethod
    def _upsert(database: Any, collection: str, username: str, body: dict[str, Any]) -> None:
        document = {**body, "username": username}
        database[collection].update_one(
            {"username": username}, {"$set": document}, upsert=True,
        )

    def cleanup(self, username: str) -> None:
        client = self._client()
        try:
            database = client[self.database]
            for collection in ALL_COLLECTIONS:
                database[collection].delete_many({"username": username})
        finally:
            client.close()

    def seed(self, username: str, snapshot: dict[str, Any]) -> None:
        username = username.lower()
        self.cleanup(username)
        position = snapshot["position"]
        overrides = snapshot["player_info_overrides"]
        info = {
            "password": FIXED_BCRYPT_HASH,
            "email": f"{username}@kaetram-replay.invalid",
            "x": int(position[0]),
            "y": int(position[1]),
            "userAgent": "kaetram-reachability-replay",
            "rank": 0,
            "poison": {"type": -1, "remaining": -1},
            "effects": {},
            "hitPoints": int(snapshot["hit_points"]),
            "mana": int(snapshot["mana"]),
            "orientation": 1,
            "ban": 0,
            "jail": 0,
            "mute": 0,
            "lastWarp": 0,
            "mapVersion": -1,
            "regionsLoaded": [],
            "friends": [],
            "lastServerId": 1,
            "lastAddress": "127.0.0.1",
            "lastGlobalChat": 0,
            "guild": "",
            "pet": "",
            **overrides,
        }
        quests = [dict(quest) for quest in snapshot["quests"]]
        if not any(quest.get("key") == "tutorial" for quest in quests):
            quests.insert(0, dict(TUTORIAL_FINISHED_QUEST))
        client = self._client()
        try:
            database = client[self.database]
            self._upsert(database, "player_info", username, info)
            self._upsert(
                database, "player_inventory", username,
                {"slots": _inventory_slots(snapshot["inventory"])},
            )
            self._upsert(
                database, "player_bank", username,
                {"slots": _inventory_slots(snapshot["bank"])},
            )
            self._upsert(
                database, "player_equipment", username,
                {"equipments": snapshot["equipment"]},
            )
            self._upsert(database, "player_quests", username, {"quests": quests})
            self._upsert(
                database, "player_achievements", username,
                {"achievements": snapshot["achievements"]},
            )
            self._upsert(
                database, "player_skills", username,
                {"skills": _normalize_skills(snapshot["skills"])},
            )
            self._upsert(database, "player_statistics", username, snapshot["statistics"])
        finally:
            client.close()

    def snapshot(self, username: str) -> dict[str, Any]:
        client = self._client()
        try:
            database = client[self.database]
            return {
                collection: database[collection].find_one(
                    {"username": username.lower()}, {"_id": 0},
                )
                for collection in ALL_COLLECTIONS
            }
        finally:
            client.close()


@dataclass
class ToolResult:
    is_error: bool
    text: str

    def json(self) -> dict[str, Any] | None:
        body = self.text
        prefix = re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*:\s+", body)
        if prefix:
            body = body[prefix.end():]
        for separator in ("\n\nASCII_MAP:", "\n\nDIGEST:", "\n\nSTUCK_CHECK:"):
            if separator in body:
                body = body.split(separator, 1)[0]
                break
        try:
            value = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None


class ColdMcpHandle:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        result = await self._session.call_tool(name, arguments)
        parts = [
            block.text if hasattr(block, "text") else str(block)
            for block in (result.content or [])
        ]
        return ToolResult(is_error=bool(result.isError), text="\n".join(parts))


async def _wait_for_tcp(host: str, port: int, label: str) -> None:
    deadline = asyncio.get_running_loop().time() + 20
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError as exc:
            last_error = exc
            await asyncio.sleep(0.5)
    raise RuntimeError(f"{label} {host}:{port} is unreachable: {last_error}")


@asynccontextmanager
async def cold_mcp_session(*, username: str, client_url: str, server_port: str):
    """Start one cold MCP/browser session and close it at a save boundary."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parsed = urlparse(client_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(f"invalid explicit client URL: {client_url!r}")
    await _wait_for_tcp(
        os.environ.get("GAME_WS_HOST", "localhost"), int(server_port), "game server",
    )
    await _wait_for_tcp(
        parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), "web client",
    )
    environment = {
        **os.environ,
        "KAETRAM_USERNAME": username,
        "KAETRAM_PASSWORD": "test",
        "KAETRAM_CLIENT_URL": client_url,
        "KAETRAM_PORT": server_port,
        "KAETRAM_EXTRACTOR": str(STATE_EXTRACTOR),
        "KAETRAM_STATE_DIR": f"/tmp/kaetram_reachability/{username}",
        "KAETRAM_HEADED": "0",
        "KAETRAM_TEST_LANE": "0",
    }
    parameters = StdioServerParameters(
        command=sys.executable, args=[str(MCP_SERVER)], env=environment,
    )
    transport = stdio_client(parameters)
    read, write = await transport.__aenter__()
    session = ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120))
    try:
        await session.__aenter__()
        try:
            await session.initialize()
            handle = ColdMcpHandle(session)
            last_result: ToolResult | None = None
            for _ in range(30):
                last_result = await handle.call_tool("observe", {})
                payload = last_result.json()
                if (
                    not last_result.is_error and isinstance(payload, dict)
                    and isinstance(payload.get("pos"), dict)
                    and isinstance(payload.get("inventory"), list)
                ):
                    break
                await asyncio.sleep(0.5)
            else:
                diagnostic = last_result.text[:500] if last_result else "no observe result"
                raise RuntimeError(f"cold MCP session did not become ready: {diagnostic}")
            yield handle
        finally:
            await session.__aexit__(None, None, None)
    finally:
        await transport.__aexit__(None, None, None)
        # The game server persists the player on websocket disconnect.
        await asyncio.sleep(2)
