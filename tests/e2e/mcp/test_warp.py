"""warp() tool — all documented warp destinations dispatch cleanly."""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.parametrize("location", [
    "mudwich", "aynor", "lakesworld", "crullfield", "patsow", "undersea",
])
@pytest.mark.mcp_full
async def test_layerB_warp_all_documented_locations(isolated_lane, unique_username, location):
    """Every documented warp id must dispatch — either success or a clean
    gate error (some zones require achievements, e.g. undersea needs
    waterguardian). Never a crash/silent failure."""
    seed_player(
        unique_username,
        position=(188, 157),
        inventory=[{"key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            await session.call_tool("observe", {})
            res = await session.call_tool("warp", {"location": location})
            assert res.text, f"empty response warping to {location}"
            if res.is_error:
                text = res.text.lower()
                assert any(k in text for k in (
                    "achievement", "quest", "gate", "require", "error", "locked",
                )), f"warp {location} errored without rationale: {res.text[:200]}"
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_warp_unknown_location_returns_error(isolated_lane, unique_username):
    """Unknown warp id must produce a clear signal (error or allowed-list)."""
    seed_player(
        unique_username,
        position=(188, 157),
        inventory=[{"key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            await session.call_tool("observe", {})
            res = await session.call_tool("warp", {"location": "notarealplace"})
            data = res.json() or {}
            text = res.text.lower()
            assert (
                res.is_error
                or "error" in data
                or "unknown" in text
                or "allowed" in data
            ), f"no error signal for bad warp: {res.text[:200]}"
    finally:
        cleanup_player(unique_username)
