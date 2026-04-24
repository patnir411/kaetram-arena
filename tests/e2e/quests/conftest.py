"""Quest chain test helpers — assertion utilities for deterministic quest e2e tests."""
from __future__ import annotations

import asyncio
from typing import Any

from bench.seed import snapshot_player

AUTOSAVE_WAIT = 5.0  # seconds to wait after mcp_session closes for Kaetram to autosave


def _quest_entry(snap: dict[str, Any], quest_key: str) -> dict[str, Any] | None:
    quests = (snap.get("player_quests") or {}).get("quests") or []
    return next((q for q in quests if q.get("key") == quest_key), None)


def _quest_stage(snap: dict[str, Any], quest_key: str) -> int:
    q = _quest_entry(snap, quest_key)
    return int((q or {}).get("stage", 0) or 0)


def assert_quest_state(
    username: str,
    quest_key: str,
    *,
    stage: int,
    sub_stage: int | None = None,
    completed_sub_stages: list[str] | None = None,
) -> None:
    snap = snapshot_player(username)
    quest = _quest_entry(snap, quest_key)
    assert quest is not None, (
        f"{quest_key}: quest missing from snapshot. "
        f"quests={[(q.get('key'), q.get('stage')) for q in (snap.get('player_quests') or {}).get('quests') or []]}"
    )

    actual_stage = int(quest.get("stage", 0) or 0)
    actual_sub_stage = int(quest.get("subStage", 0) or 0)
    actual_completed = list(quest.get("completedSubStages") or [])
    assert actual_stage == stage, (
        f"{quest_key}: expected stage={stage}, got {actual_stage}. quest={quest}"
    )
    if sub_stage is not None:
        assert actual_sub_stage == sub_stage, (
            f"{quest_key}: expected subStage={sub_stage}, got {actual_sub_stage}. quest={quest}"
        )
    if completed_sub_stages is not None:
        assert actual_completed == completed_sub_stages, (
            f"{quest_key}: expected completedSubStages={completed_sub_stages}, "
            f"got {actual_completed}. quest={quest}"
        )


async def wait_for_quest_state(
    username: str,
    quest_key: str,
    *,
    stage: int,
    sub_stage: int | None = None,
    completed_sub_stages: list[str] | None = None,
    polls: int = 10,
    delay_s: float = 0.5,
) -> dict[str, Any]:
    last_quest: dict[str, Any] | None = None
    for attempt in range(polls):
        snap = snapshot_player(username)
        quest = _quest_entry(snap, quest_key)
        last_quest = quest
        if quest is not None:
            actual_stage = int(quest.get("stage", 0) or 0)
            actual_sub_stage = int(quest.get("subStage", 0) or 0)
            actual_completed = list(quest.get("completedSubStages") or [])
            if (
                actual_stage == stage
                and (sub_stage is None or actual_sub_stage == sub_stage)
                and (completed_sub_stages is None or actual_completed == completed_sub_stages)
            ):
                return quest
        if attempt < polls - 1:
            await asyncio.sleep(delay_s)

    raise AssertionError(
        f"{quest_key}: quest state did not reach stage={stage}, subStage={sub_stage}, "
        f"completedSubStages={completed_sub_stages}. last quest={last_quest}"
    )


def assert_quest_stage(username: str, quest_key: str, expected_min: int) -> None:
    snap = snapshot_player(username)
    stage = _quest_stage(snap, quest_key)
    assert stage >= expected_min, (
        f"{quest_key}: expected stage>={expected_min}, got {stage}. "
        f"quests={[(q.get('key'), q.get('stage')) for q in (snap.get('player_quests') or {}).get('quests') or []]}"
    )


def assert_quest_finished(username: str, quest_key: str, stage_count: int) -> None:
    assert_quest_stage(username, quest_key, stage_count)


def read_quests_from_db(username: str) -> list[dict]:
    """Read current quest list from Mongo — use when re-seeding across phases."""
    snap = snapshot_player(username)
    return (snap.get("player_quests") or {}).get("quests") or []


def saved_inventory_slots(username: str) -> list[dict[str, Any]]:
    snap = snapshot_player(username)
    return (snap.get("player_inventory") or {}).get("slots") or []


def count_live_inventory(items: list[dict[str, Any]] | None, item_key: str) -> int:
    def _normalize(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    wanted = _normalize(item_key)
    total = 0
    for item in items or []:
        key = _normalize(str(item.get("key") or ""))
        name = _normalize(str(item.get("name") or ""))
        if wanted not in {key, name} and wanted not in name and wanted not in key:
            continue
        total += int(item.get("count") or 1)
    return total


def count_saved_inventory(username: str, item_key: str) -> int:
    return count_live_inventory(saved_inventory_slots(username), item_key)


async def live_observe(session) -> dict[str, Any]:
    return (await session.call_tool("observe", {})).json() or {}


async def wait_for_inventory_count(
    session,
    item_key: str,
    *,
    expected_at_least: int,
    polls: int = 30,
    delay_s: float = 0.5,
) -> dict[str, Any]:
    last_obs: dict[str, Any] = {}
    for _ in range(polls):
        last_obs = await live_observe(session)
        count = count_live_inventory(last_obs.get("inventory") or [], item_key)
        if count >= expected_at_least:
            return last_obs
        await asyncio.sleep(delay_s)
    raise AssertionError(
        f"live inventory never reached {expected_at_least}x {item_key}; "
        f"last inventory={last_obs.get('inventory')}"
    )


async def wait_for_position(
    session,
    *,
    x: int,
    y: int,
    max_distance: int = 1,
    polls: int = 20,
    delay_s: float = 1.0,
) -> dict[str, Any]:
    last_obs: dict[str, Any] = {}
    for _ in range(polls):
        last_obs = await live_observe(session)
        pos = last_obs.get("pos") or {}
        if abs(int(pos.get("x", -999)) - x) + abs(int(pos.get("y", -999)) - y) <= max_distance:
            return last_obs
        await asyncio.sleep(delay_s)
    raise AssertionError(
        f"player never reached ({x},{y}) within distance {max_distance}; "
        f"last pos={(last_obs.get('pos') or {})}"
    )


async def gather_until_count(
    session,
    *,
    resource_name: str,
    item_key: str,
    target_count: int,
    attempts: int | None = None,
    polls_after_gather: int = 30,
    delay_after_gather_s: float = 0.5,
) -> dict[str, Any]:
    max_attempts = attempts or target_count + 3
    last_obs = await live_observe(session)
    current = count_live_inventory(last_obs.get("inventory") or [], item_key)
    while current < target_count and max_attempts > 0:
        result = await session.call_tool("gather", {"resource_name": resource_name})
        assert not result.is_error, f"gather({resource_name}) errored: {result.text[:300]}"
        last_obs = await wait_for_inventory_count(
            session,
            item_key,
            expected_at_least=current + 1,
            polls=polls_after_gather,
            delay_s=delay_after_gather_s,
        )
        current = count_live_inventory(last_obs.get("inventory") or [], item_key)
        max_attempts -= 1

    assert current >= target_count, (
        f"expected at least {target_count}x {item_key} from {resource_name}, got {current}. "
        f"last inventory={last_obs.get('inventory')}"
    )
    return last_obs


async def craft_recipe(session, *, skill: str, recipe_key: str, count: int) -> dict[str, Any]:
    result = await session.call_tool(
        "craft_item",
        {"skill": skill, "recipe_key": recipe_key, "count": count},
    )
    assert not result.is_error, f"craft_item({skill}, {recipe_key}) errored: {result.text[:300]}"
    data = result.json() or {}
    assert "error" not in data, f"craft_item({skill}, {recipe_key}) returned error: {data}"
    return data
