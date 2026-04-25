"""Quest chain test helpers — assertion utilities for deterministic quest e2e tests."""
from __future__ import annotations

import asyncio
from typing import Any

from bench.seed import snapshot_player
from tests.e2e.quests.reachability.debug import get_current_test_debug

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
    debug = get_current_test_debug()
    result = await session.call_tool("observe", {})
    if debug is not None:
        debug.action(
            tool="observe",
            args={},
            ok=not result.is_error,
            result_preview=(result.text or "")[:240] if result.text else None,
            error=result.text[:240] if result.is_error else None,
        )
    data = result.json() or {}
    if debug is not None:
        debug.snapshot("live_observe", data)
    return data


async def wait_for_inventory_count(
    session,
    item_key: str,
    *,
    expected_at_least: int,
    polls: int = 30,
    delay_s: float = 0.5,
) -> dict[str, Any]:
    debug = get_current_test_debug()
    last_obs: dict[str, Any] = {}
    for attempt in range(polls):
        last_obs = await live_observe(session)
        count = count_live_inventory(last_obs.get("inventory") or [], item_key)
        if count >= expected_at_least:
            if debug is not None:
                debug.event(
                    "inventory_reached",
                    item_key=item_key,
                    expected_at_least=expected_at_least,
                    actual=count,
                    attempt=attempt + 1,
                )
            return last_obs
        await asyncio.sleep(delay_s)
    if debug is not None:
        debug.snapshot(f"wait_for_inventory_count_failed_{item_key}", last_obs)
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
    debug = get_current_test_debug()
    last_obs: dict[str, Any] = {}
    for attempt in range(polls):
        last_obs = await live_observe(session)
        pos = last_obs.get("pos") or {}
        if abs(int(pos.get("x", -999)) - x) + abs(int(pos.get("y", -999)) - y) <= max_distance:
            if debug is not None:
                debug.event(
                    "position_reached",
                    target=(x, y),
                    max_distance=max_distance,
                    actual=(int(pos.get("x", -999)), int(pos.get("y", -999))),
                    attempt=attempt + 1,
                )
            return last_obs
        await asyncio.sleep(delay_s)
    if debug is not None:
        debug.snapshot(f"wait_for_position_failed_{x}_{y}", last_obs)
    raise AssertionError(
        f"player never reached ({x},{y}) within distance {max_distance}; "
        f"last pos={(last_obs.get('pos') or {})}"
    )


async def traverse_door(
    session,
    *,
    door_x: int,
    door_y: int,
    exit_x: int,
    exit_y: int,
    max_distance: int = 5,
    polls: int = 15,
    delay_s: float = 1.0,
) -> dict[str, Any]:
    """Step onto a door tile and wait for the expected teleport exit.

    Some door tiles are sensitive to approach direction. If the first
    navigate-to-door attempt stalls in place, try a few adjacent approach
    tiles and retry the door step before failing.
    """
    debug = get_current_test_debug()
    approach_tiles = [
        (door_x, door_y + 1),
        (door_x + 1, door_y),
        (door_x - 1, door_y),
        (door_x, door_y - 1),
    ]

    last_obs: dict[str, Any] = {}
    for attempt, (approach_x, approach_y) in enumerate(approach_tiles, start=1):
        last_obs = await live_observe(session)
        pos = last_obs.get("pos") or {}
        current = (int(pos.get("x", -999)), int(pos.get("y", -999)))
        if current != (approach_x, approach_y):
            move = await session.call_tool("navigate", {"x": approach_x, "y": approach_y})
            if debug is not None:
                debug.action(
                    tool="navigate",
                    args={
                        "x": approach_x,
                        "y": approach_y,
                        "_door_approach": True,
                        "_door": (door_x, door_y),
                        "_attempt": attempt,
                    },
                    ok=not move.is_error,
                    result_preview=(move.text or "")[:240] if move.text else None,
                    error=move.text[:240] if move.is_error else None,
                )

        # `navigate` already uses `__navigateTo` which falls through to a
        # short-path direct go() for distances <= 15, including onto door
        # tiles (state_extractor patches map.grid for door targets).
        step = await session.call_tool("navigate", {"x": door_x, "y": door_y})
        if debug is not None:
            debug.action(
                tool="navigate",
                args={"x": door_x, "y": door_y, "_door_step": True, "_attempt": attempt},
                ok=not step.is_error,
                result_preview=(step.text or "")[:240] if step.text else None,
                error=step.text[:240] if step.is_error else None,
            )
        assert not step.is_error, step.text[:300]

        try:
            return await wait_for_position(
                session,
                x=exit_x,
                y=exit_y,
                max_distance=max_distance,
                polls=polls,
                delay_s=delay_s,
            )
        except AssertionError:
            try:
                await session.call_tool("cancel_nav", {})
            except Exception:
                pass
            if debug is not None:
                debug.event(
                    "door_attempt_failed",
                    attempt=attempt,
                    door=(door_x, door_y),
                    exit=(exit_x, exit_y),
                )

    if debug is not None:
        debug.snapshot(f"traverse_door_failed_{door_x}_{door_y}", last_obs)
    raise AssertionError(
        f"door traversal never reached ({exit_x},{exit_y}) from door ({door_x},{door_y}); "
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
    debug = get_current_test_debug()
    max_attempts = attempts or target_count + 3
    last_obs = await live_observe(session)
    current = count_live_inventory(last_obs.get("inventory") or [], item_key)
    while current < target_count and max_attempts > 0:
        result = await session.call_tool("gather", {"resource_name": resource_name})
        if debug is not None:
            debug.action(
                tool="gather",
                args={"resource_name": resource_name},
                ok=not result.is_error,
                result_preview=(result.text or "")[:240] if result.text else None,
                error=result.text[:240] if result.is_error else None,
            )
        assert not result.is_error, f"gather({resource_name}) errored: {result.text[:300]}"
        # Resource gathering is probabilistic — a single attempt may yield 0
        # items even when the player has the right tool and level. Poll
        # briefly for an inventory update; if nothing arrives, count this
        # attempt as a miss and try again instead of failing the test.
        for _ in range(polls_after_gather):
            await asyncio.sleep(delay_after_gather_s)
            last_obs = await live_observe(session)
            new_count = count_live_inventory(last_obs.get("inventory") or [], item_key)
            if new_count > current:
                current = new_count
                break
        if debug is not None:
            debug.event(
                "gather_progress",
                resource_name=resource_name,
                item_key=item_key,
                current=current,
                target=target_count,
                attempts_remaining=max_attempts - 1,
            )
        max_attempts -= 1

    if debug is not None and current < target_count:
        debug.snapshot(f"gather_until_count_failed_{resource_name}", last_obs)
    assert current >= target_count, (
        f"expected at least {target_count}x {item_key} from {resource_name}, got {current}. "
        f"last inventory={last_obs.get('inventory')}"
    )
    return last_obs


async def craft_recipe(session, *, skill: str, recipe_key: str, count: int) -> dict[str, Any]:
    debug = get_current_test_debug()
    result = await session.call_tool(
        "craft_item",
        {"skill": skill, "recipe_key": recipe_key, "count": count},
    )
    if debug is not None:
        debug.action(
            tool="craft_item",
            args={"skill": skill, "recipe_key": recipe_key, "count": count},
            ok=not result.is_error,
            result_preview=(result.text or "")[:600] if result.text else None,
            error=result.text[:600] if result.is_error else None,
        )
    assert not result.is_error, f"craft_item({skill}, {recipe_key}) errored: {result.text[:300]}"
    data = result.json() or {}
    assert "error" not in data, f"craft_item({skill}, {recipe_key}) returned error: {data}"
    if debug is not None:
        debug.event("craft_succeeded", skill=skill, recipe_key=recipe_key, count=count)
    return data
