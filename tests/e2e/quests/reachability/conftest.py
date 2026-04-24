"""Reachability-test helpers.

These tests ask whether a **vanilla post-tutorial player** — spawned at
Mudwich with only the tutorial starter kit — can physically reach each
discrete step of a Core-5 quest using only MCP tools. They are intentionally
separate from:

  - `core/` stage-transition tests: pre-seed every prereq to isolate runtime
    quest-system transitions.
  - `core/integration/`: compose stages into an end-to-end quest run (still
    pre-seeds resource counts to keep runtime bounded).

Reachability tests deliberately MINIMIZE the seed. They exist to catch
benchmark fairness bugs — hidden region gates, stale NPC coords, missing
resource placements, unsurvivable boss fights — that the other two tiers
silently paper over.

Most tests live under budgets that assume the agent driving the test is
`navigate`, not the LLM. We're proving the *tool path* is walkable, not
that the agent can decide to use it.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.e2e.quests.conftest import live_observe
from tests.e2e.quests.reachability.debug import TestDebugLog

# Tutorial bypass grants this exact starter kit — see
# Kaetram-Open/packages/server/src/game/entity/character/player/quests.ts
# `applyTutorialBypass()`.
VANILLA_STARTER_KIT: list[dict[str, Any]] = [
    {"index": 0, "key": "bronzeaxe", "count": 1},
    {"index": 1, "key": "knife", "count": 1},
    {"index": 2, "key": "fishingpole", "count": 1},
    {"index": 3, "key": "coppersword", "count": 1},
    {"index": 4, "key": "woodenbow", "count": 1},
]

# Mudwich central spawn — warps.ts landing tile for `mudwich`.
MUDWICH_SPAWN: tuple[int, int] = (188, 157)


def vanilla_seed_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return a `seed_player(**kwargs)`-compatible dict for a fresh
    post-tutorial spawn at Mudwich with the starter kit.

    Caller may pass `position=`, `skills=`, etc. to override specific fields
    — the default is "nothing pre-granted beyond what the tutorial bypass
    gives a real player."
    """
    base = {
        "position": MUDWICH_SPAWN,
        "hit_points": 100,
        "mana": 20,
        "inventory": list(VANILLA_STARTER_KIT),
    }
    base.update(overrides)
    return base


def _nav_log(msg: str) -> None:
    import os
    import sys
    if os.environ.get("KAETRAM_NAV_DEBUG", "0") not in {"0", "false", ""}:
        print(f"[navigate_long] {msg}", file=sys.stderr, flush=True)


async def navigate_long(
    session,
    *,
    target_x: int,
    target_y: int,
    max_step: int = 50,
    max_hops: int = 25,
    arrive_tolerance: int = 3,
    per_hop_timeout_s: float = 90.0,
    poll_interval_s: float = 2.0,
    no_progress_timeout_s: float = 45.0,
    debug: TestDebugLog | None = None,
) -> dict[str, Any]:
    """Chain `navigate` calls to reach a faraway target.

    The MCP `navigate` tool tops out at 100 tiles per call. Cross-region
    walks (Mudwich → Rick at ~1500 tiles, Mudwich → Herbalist at ~270
    tiles) must be decomposed into hops.

    Per-hop loop:
      1. Read current pos via `observe`.
      2. Pick a hop target `max_step` tiles toward the destination along
         whichever axis has the longer remainder.
      3. Issue `navigate(hop_x, hop_y)`.
      4. Poll `observe` until:
           - Manhattan distance to hop target <= `arrive_tolerance`    (success)
           - `navigation.status` reported as "arrived"                  (success)
           - `navigation.status` reported as "stuck"                    (re-plan)
           - Position has not changed for `no_progress_timeout_s`       (re-plan)
           - `per_hop_timeout_s` elapsed                                (re-plan)
      5. Re-plan from the new current position.

    The outer loop gives up after `max_hops` unsuccessful hops.
    """
    import time as _time

    obs = await live_observe(session)
    if debug is not None:
        debug.event("navigate_long_start", target=(target_x, target_y),
                    max_step=max_step, max_hops=max_hops)
        debug.snapshot("navigate_long_initial", obs)
    for hop in range(max_hops):
        pos = obs.get("pos") or {}
        cx = int(pos.get("x", -1))
        cy = int(pos.get("y", -1))
        if cx < 0 or cy < 0:
            raise AssertionError(f"navigate_long: bad pos in observe: {pos!r}")

        dx_total = target_x - cx
        dy_total = target_y - cy
        if abs(dx_total) + abs(dy_total) <= arrive_tolerance:
            return obs

        # Pick hop target: either the destination (if within one hop) or
        # `max_step` tiles along the larger axis.
        if abs(dx_total) <= max_step and abs(dy_total) <= max_step:
            hop_x, hop_y = target_x, target_y
        elif abs(dx_total) >= abs(dy_total):
            hop_x = cx + max(-max_step, min(max_step, dx_total))
            hop_y = cy
        else:
            hop_x = cx
            hop_y = cy + max(-max_step, min(max_step, dy_total))

        _nav_log(f"hop {hop}: ({cx},{cy}) -> ({hop_x},{hop_y}) "
                 f"[remaining: dx={dx_total}, dy={dy_total}]")
        result = await session.call_tool("navigate", {"x": hop_x, "y": hop_y})
        if debug is not None:
            preview = (result.text or "")[:240] if result.text else None
            debug.action(
                tool="navigate",
                args={"x": hop_x, "y": hop_y, "_hop": hop, "_from": (cx, cy)},
                ok=not result.is_error,
                result_preview=preview,
                error=(result.text[:240] if result.is_error else None),
            )
        assert not result.is_error, f"navigate hop {hop} errored: {result.text[:300]}"

        hop_start = _time.monotonic()
        last_progress_at = hop_start
        last_px, last_py = cx, cy
        exit_reason = "timeout"

        while True:
            now = _time.monotonic()
            if now - hop_start > per_hop_timeout_s:
                exit_reason = "per_hop_timeout"
                break

            await asyncio.sleep(poll_interval_s)
            obs = await live_observe(session)
            pos = obs.get("pos") or {}
            px = int(pos.get("x", -1))
            py = int(pos.get("y", -1))
            nav_state = (obs.get("navigation") or {}).get("status")

            if abs(px - hop_x) + abs(py - hop_y) <= arrive_tolerance:
                exit_reason = "at_hop"
                break
            if nav_state == "arrived":
                exit_reason = "nav_arrived"
                break
            if nav_state == "stuck":
                exit_reason = "nav_stuck"
                break

            if (px, py) != (last_px, last_py):
                last_px, last_py = px, py
                last_progress_at = now
            elif now - last_progress_at > no_progress_timeout_s:
                exit_reason = "no_progress"
                break

        _nav_log(f"hop {hop}: ended at ({last_px},{last_py}) reason={exit_reason} "
                 f"moved={abs(last_px-cx)+abs(last_py-cy)} elapsed={now-hop_start:.1f}s")
        if debug is not None:
            debug.event(
                "hop_end",
                hop=hop,
                start=(cx, cy),
                target=(hop_x, hop_y),
                end=(last_px, last_py),
                moved=abs(last_px - cx) + abs(last_py - cy),
                reason=exit_reason,
                elapsed_s=round(now - hop_start, 2),
            )
            # On anything other than a clean at_hop/nav_arrived arrival,
            # snapshot current state + STUCK_CHECK so we can see why.
            if exit_reason in ("per_hop_timeout", "no_progress", "nav_stuck"):
                debug.snapshot(f"hop_{hop}_stall", obs)
                # STUCK_CHECK trailer from the most recent observe
                stuck = None
                try:
                    # We don't have the raw ToolResult here — re-observe to
                    # fetch the STUCK_CHECK trailer via a fresh call.
                    r = await session.call_tool("observe", {})
                    debug.action("observe", args={},
                                 ok=not r.is_error,
                                 result_preview=(r.text or "")[:240])
                    stuck = r.observe_stuck_check()
                except Exception:
                    pass
                if stuck is not None:
                    debug.event("stuck_check", hop=hop, stuck=stuck)
        # Loop back — outer for-loop re-observes and re-plans.

    raise AssertionError(
        f"navigate_long: failed to reach ({target_x},{target_y}) within "
        f"{max_hops} hops. Last pos={(obs.get('pos') or {})}, "
        f"nav={(obs.get('navigation') or {})}"
    )


async def assert_pos_within(
    session, *, target_x: int, target_y: int, tolerance: int = 3
) -> dict[str, Any]:
    """Observe and assert the player is within `tolerance` tiles of target."""
    obs = await live_observe(session)
    pos = obs.get("pos") or {}
    x = int(pos.get("x", -999))
    y = int(pos.get("y", -999))
    manhattan = abs(x - target_x) + abs(y - target_y)
    assert manhattan <= tolerance, (
        f"expected pos within {tolerance} tiles of ({target_x},{target_y}), "
        f"got ({x},{y}) — manhattan={manhattan}"
    )
    return obs


# Pytest marker convention for this directory.
reachability = pytest.mark.reachability
slow = pytest.mark.slow


@pytest.fixture
def test_debug(request):
    """Per-test debug collector. No-op unless KAETRAM_DEBUG=1 is set.

    Writes a JSONL trace to `sandbox/<slot>/reachability_logs/<test_name>.jsonl`
    and prints a compact summary to stderr at test end. Use `.action()`,
    `.event()`, `.snapshot()` on it from inside a test, OR pass it via the
    `debug=` kwarg to `navigate_long` / `logged_call_tool`.
    """
    name = request.node.name.replace("/", "_")
    dbg = TestDebugLog(test_name=name)
    status = "FAIL"

    def _mark_pass():
        nonlocal status
        status = "PASS"

    # Expose a hook so tests can bump status to PASS on success — we also
    # detect via finalizer whether the test raised.
    dbg._mark_pass = _mark_pass  # type: ignore[attr-defined]

    yield dbg

    # Pytest report isn't directly queryable here without a plugin, so we
    # infer: if no exception propagated into the fixture teardown path,
    # treat as PASS. This is imperfect but good enough for summary lines.
    try:
        # `request.node.rep_call` is set by pytest_runtest_makereport if
        # we wire one; we don't, so fall back to no-exception heuristic.
        rep = getattr(request.node, "rep_call", None)
        if rep is not None:
            status = "PASS" if rep.passed else ("SKIP" if rep.skipped else "FAIL")
    except Exception:
        pass
    dbg.close(status=status)


def pytest_runtest_makereport(item, call):
    """Record call phase outcome on the item so fixtures can read it on
    teardown. Standard pytest idiom."""
    if call.when == "call":
        outcome_rep = getattr(call, "excinfo", None)
        passed = outcome_rep is None
        # Build a minimal object with `.passed` + `.skipped` attrs.
        class _Rep:
            pass
        rep = _Rep()
        rep.passed = passed
        rep.skipped = False
        item.rep_call = rep
