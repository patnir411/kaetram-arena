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
from tests.e2e.quests.reachability.debug import (
    TestDebugLog,
    get_current_test_debug,
    reset_current_test_debug,
    set_current_test_debug,
)

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

# Reachability tests are about map/interaction access, not survivability under
# incidental aggro. Give the default seed a large HP buffer so long walks do
# not fail on unrelated combat variance. Individual tests can still override
# `hit_points=` when they need a specific combat envelope.
REACHABILITY_HP_BUFFER = 3039
REACHABILITY_NO_PROGRESS_TIMEOUT_S = 10.0
REACHABILITY_HEALTH_XP = 15_000_000


def vanilla_seed_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return a `seed_player(**kwargs)`-compatible dict for a fresh
    post-tutorial spawn at Mudwich with the starter kit and boosted HP.

    Caller may pass `position=`, `skills=`, etc. to override specific fields
    — the default is "nothing pre-granted beyond what the tutorial bypass
    gives a real player", except for the reachability HP buffer that keeps
    nav-only assertions from failing due to stray mob damage.
    """
    base = {
        "position": MUDWICH_SPAWN,
        "hit_points": REACHABILITY_HP_BUFFER,
        "mana": 20,
        "inventory": list(VANILLA_STARTER_KIT),
        "skills": [{"type": 3, "experience": REACHABILITY_HEALTH_XP}],
    }
    merged = dict(base)
    merged.update(overrides)

    override_skills = list(overrides.get("skills") or [])
    if override_skills:
        has_health = any(int(skill.get("type", -1)) == 3 for skill in override_skills)
        if not has_health:
            override_skills.append({"type": 3, "experience": REACHABILITY_HEALTH_XP})
        merged["skills"] = override_skills

    if "inventory" in overrides and overrides["inventory"] is not None:
        merged["inventory"] = list(overrides["inventory"])

    return merged


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
    no_progress_timeout_s: float = REACHABILITY_NO_PROGRESS_TIMEOUT_S,
    navigate_call_timeout_s: float = 10.0,
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
    if debug is None:
        debug = get_current_test_debug()
    _phase_start_t = _time.monotonic()
    if debug is not None:
        debug.event("phase_start", phase="navigate_long",
                    target=(target_x, target_y), max_step=max_step, max_hops=max_hops)

    async def _capture_failure_probe(label: str, **fields: Any) -> None:
        if debug is None:
            return
        debug.event(label, **fields)
        try:
            r = await session.call_tool("observe", {})
            debug.action(
                "observe",
                args={"_probe": label, **fields},
                ok=not r.is_error,
                result_preview=(r.text or "")[:240] if r.text else None,
                error=(r.text[:240] if r.is_error and r.text else None),
            )
            debug.raw_observe(label, r.text or "")
            stuck = r.observe_stuck_check()
            if stuck is not None:
                debug.event(f"{label}_stuck_check", **fields, stuck=stuck)
        except Exception as exc:
            debug.event(f"{label}_probe_error", **fields, error=str(exc))

    def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def _escape_candidates(
        cx: int, cy: int, target_x: int, target_y: int, radius: int = 12
    ) -> list[tuple[int, int]]:
        dx = target_x - cx
        dy = target_y - cy
        candidates: list[tuple[int, int]] = []

        # Prefer stepping perpendicular to the current main heading first to
        # break out of local tree/rock pockets, then try reversing the axis.
        if abs(dx) >= abs(dy):
            candidates.extend([
                (cx, cy - radius),
                (cx, cy + radius),
                (cx - radius if dx > 0 else cx + radius, cy),
                (cx + radius if dx > 0 else cx - radius, cy),
            ])
        else:
            candidates.extend([
                (cx - radius, cy),
                (cx + radius, cy),
                (cx, cy - radius if dy > 0 else cy + radius),
                (cx, cy + radius if dy > 0 else cy - radius),
            ])

        # Final fallback: all four cardinals in case the preferred ordering is blocked.
        candidates.extend([
            (cx - radius, cy),
            (cx + radius, cy),
            (cx, cy - radius),
            (cx, cy + radius),
        ])

        deduped: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for cand in candidates:
            if cand in seen:
                continue
            seen.add(cand)
            deduped.append(cand)
        return deduped

    def _hop_candidates(cx: int, cy: int, dx_total: int, dy_total: int) -> list[tuple[int, int]]:
        close_enough = abs(dx_total) <= max_step and abs(dy_total) <= max_step

        sx = 0 if dx_total == 0 else (1 if dx_total > 0 else -1)
        sy = 0 if dy_total == 0 else (1 if dy_total > 0 else -1)
        step_x = abs(dx_total) if close_enough else min(max_step, abs(dx_total))
        step_y = abs(dy_total) if close_enough else min(max_step, abs(dy_total))
        half_step = max(12, max_step // 2)

        candidates: list[tuple[int, int]] = []
        if close_enough:
            candidates.append((target_x, target_y))
        if abs(dx_total) >= abs(dy_total):
            candidates.extend([
                (cx + sx * step_x, cy),
                (cx, cy + sy * step_y),
                (cx + sx * half_step, cy + sy * half_step),
                (cx + sx * half_step, cy - sy * half_step if sy else cy + half_step),
                (cx + sx * half_step, cy + sy * half_step if sy else cy - half_step),
            ])
        else:
            candidates.extend([
                (cx, cy + sy * step_y),
                (cx + sx * step_x, cy),
                (cx + sx * half_step, cy + sy * half_step),
                (cx - sx * half_step if sx else cx + half_step, cy + sy * half_step),
                (cx + sx * half_step if sx else cx - half_step, cy + sy * half_step),
            ])

        deduped: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for cand in candidates:
            if cand in seen or cand == (cx, cy):
                continue
            seen.add(cand)
            deduped.append(cand)
        return deduped

    async def _attempt_escape(
        *,
        cluster_origin: tuple[int, int],
        hop: int,
    ) -> dict[str, Any] | None:
        for esc_x, esc_y in _escape_candidates(cluster_origin[0], cluster_origin[1], target_x, target_y):
            try:
                result = await asyncio.wait_for(
                    session.call_tool("navigate", {"x": esc_x, "y": esc_y}),
                    timeout=navigate_call_timeout_s,
                )
            except asyncio.TimeoutError:
                continue

            if result.is_error:
                continue

            if debug is not None:
                debug.action(
                    tool="navigate",
                    args={"x": esc_x, "y": esc_y, "_escape": True, "_hop": hop, "_from": cluster_origin},
                    ok=True,
                    result_preview=(result.text or "")[:240] if result.text else None,
                )

            last_escape_obs: dict[str, Any] = {}
            for _ in range(4):
                await asyncio.sleep(2.0)
                last_escape_obs = await live_observe(session)
                pos = last_escape_obs.get("pos") or {}
                px = int(pos.get("x", cluster_origin[0]))
                py = int(pos.get("y", cluster_origin[1]))
                if _manhattan((px, py), cluster_origin) >= 6:
                    if debug is not None:
                        debug.event(
                            "escape_nav_succeeded",
                            hop=hop,
                            start=cluster_origin,
                            end=(px, py),
                            target=(esc_x, esc_y),
                        )
                    return last_escape_obs

                nav_state = (last_escape_obs.get("navigation") or {}).get("status")
                if nav_state == "stuck":
                    break

            if debug is not None and last_escape_obs:
                pos = last_escape_obs.get("pos") or {}
                debug.event(
                    "escape_nav_failed",
                    hop=hop,
                    start=cluster_origin,
                    end=(int(pos.get("x", cluster_origin[0])), int(pos.get("y", cluster_origin[1]))),
                    target=(esc_x, esc_y),
                )
        return None

    obs = await live_observe(session)
    replan_starts: list[tuple[int, int]] = []
    replan_distances: list[int] = []
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

        replan_starts.append((cx, cy))
        if len(replan_starts) > 6:
            replan_starts.pop(0)
        replan_distances.append(abs(dx_total := target_x - cx) + abs(dy_total := target_y - cy))
        if len(replan_distances) > 6:
            replan_distances.pop(0)

        same_cluster = sum(1 for px, py in replan_starts if _manhattan((px, py), (cx, cy)) <= 4)
        if same_cluster >= 3:
            if debug is not None:
                debug.event(
                    "same_cluster_detected",
                    hop=hop,
                    cluster=(cx, cy),
                    recent_starts=replan_starts[-6:],
                )
            escaped_obs = await _attempt_escape(cluster_origin=(cx, cy), hop=hop)
            if escaped_obs is not None:
                obs = escaped_obs
                continue
            await _capture_failure_probe(
                "same_cluster_probe",
                hop=hop,
                cluster=(cx, cy),
                target=(target_x, target_y),
                recent_starts=replan_starts[-6:],
            )
            raise AssertionError(
                f"navigate_long: repeated replans from local cluster near ({cx},{cy}) "
                f"while heading to ({target_x},{target_y})"
            )

        if len(replan_starts) >= 4:
            cluster_span = max(
                _manhattan(a, b)
                for a in replan_starts[-4:]
                for b in replan_starts[-4:]
            )
            progress_gain = replan_distances[-4] - replan_distances[-1]
            if cluster_span <= 18 and progress_gain <= 12:
                if debug is not None:
                    debug.event(
                        "oscillation_detected",
                        hop=hop,
                        recent_starts=replan_starts[-4:],
                        recent_distances=replan_distances[-4:],
                        cluster_span=cluster_span,
                        progress_gain=progress_gain,
                    )
                await _capture_failure_probe(
                    "oscillation_probe",
                    hop=hop,
                    cluster=(cx, cy),
                    target=(target_x, target_y),
                    recent_starts=replan_starts[-4:],
                    recent_distances=replan_distances[-4:],
                    cluster_span=cluster_span,
                    progress_gain=progress_gain,
                )
                raise AssertionError(
                    f"navigate_long: local oscillation near ({cx},{cy}) while heading to "
                    f"({target_x},{target_y}); recent_starts={replan_starts[-4:]}"
                )

        if abs(dx_total) + abs(dy_total) <= arrive_tolerance:
            return obs

        navigate_result = None
        navigate_payload: dict[str, Any] = {}
        hop_x = hop_y = -1
        candidate_blockers: list[dict[str, Any]] = []
        for candidate_index, (cand_x, cand_y) in enumerate(_hop_candidates(cx, cy, dx_total, dy_total)):
            hop_x, hop_y = cand_x, cand_y
            _nav_log(
                f"hop {hop}: try#{candidate_index} ({cx},{cy}) -> ({hop_x},{hop_y}) "
                f"[remaining: dx={dx_total}, dy={dy_total}]"
            )
            try:
                result = await asyncio.wait_for(
                    session.call_tool("navigate", {"x": hop_x, "y": hop_y}),
                    timeout=navigate_call_timeout_s,
                )
            except asyncio.TimeoutError as exc:
                if debug is not None:
                    debug.event(
                        "navigate_call_timeout",
                        hop=hop,
                        start=(cx, cy),
                        target=(hop_x, hop_y),
                        timeout_s=navigate_call_timeout_s,
                    )
                raise AssertionError(
                    f"navigate_long: navigate tool call timed out after "
                    f"{navigate_call_timeout_s:.1f}s on hop {hop} from ({cx},{cy}) "
                    f"to ({hop_x},{hop_y})"
                ) from exc

            preview = (result.text or "")[:240] if result.text else None
            if debug is not None:
                debug.action(
                    tool="navigate",
                    args={
                        "x": hop_x,
                        "y": hop_y,
                        "_hop": hop,
                        "_from": (cx, cy),
                        "_candidate_index": candidate_index,
                    },
                    ok=not result.is_error,
                    result_preview=preview,
                    error=(result.text[:240] if result.is_error else None),
                )
            assert not result.is_error, f"navigate hop {hop} errored: {result.text[:300]}"

            navigate_payload = result.json() or {}
            if (
                navigate_payload.get("status") == "stuck"
                and navigate_payload.get("pathfinding") == "bfs_failed"
            ):
                candidate_blockers.append(
                    {"target": (hop_x, hop_y), "payload": navigate_payload}
                )
                if debug is not None:
                    debug.event(
                        "hop_candidate_blocked",
                        hop=hop,
                        start=(cx, cy),
                        target=(hop_x, hop_y),
                        payload=navigate_payload,
                    )
                continue

            navigate_result = result
            break

        if navigate_result is None:
            if debug is not None:
                debug.event(
                    "hop_all_candidates_blocked",
                    hop=hop,
                    start=(cx, cy),
                    blockers=candidate_blockers,
                )
            await _capture_failure_probe(
                "hop_all_candidates_blocked_probe",
                hop=hop,
                start=(cx, cy),
                target=(target_x, target_y),
                blockers=candidate_blockers,
            )
            obs = await live_observe(session)
            continue

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
            is_dead = bool(obs.get("is_dead") or (obs.get("status") or {}).get("dead"))

            if abs(px - hop_x) + abs(py - hop_y) <= arrive_tolerance:
                exit_reason = "at_hop"
                break
            if nav_state == "arrived":
                exit_reason = "nav_arrived"
                break
            if nav_state == "stuck":
                exit_reason = "nav_stuck"
                break
            if is_dead:
                exit_reason = "player_dead"
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
                await _capture_failure_probe(
                    "hop_stall_probe",
                    hop=hop,
                    start=(cx, cy),
                    end=(last_px, last_py),
                    target=(hop_x, hop_y),
                    final_target=(target_x, target_y),
                    reason=exit_reason,
                )
            if exit_reason == "player_dead":
                debug.snapshot(f"hop_{hop}_dead", obs)
                raise AssertionError(
                    f"navigate_long: player died during hop {hop} while heading to "
                    f"({target_x},{target_y}); last pos=({last_px},{last_py})"
                )
        # Loop back — outer for-loop re-observes and re-plans.

    await _capture_failure_probe(
        "navigate_long_final_failure_probe",
        final_target=(target_x, target_y),
        last_pos=((obs.get("pos") or {}).get("x"), (obs.get("pos") or {}).get("y")),
        last_nav=obs.get("navigation") or {},
    )
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


@pytest.fixture(autouse=True)
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
    token = set_current_test_debug(dbg)

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
    reset_current_test_debug(token)
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
