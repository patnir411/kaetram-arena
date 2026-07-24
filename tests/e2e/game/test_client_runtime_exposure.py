"""Contract test for the browser runtime consumed by the MCP integration."""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.helpers.browser import browser_session, login_seeded_player
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.mcp_smoke
async def test_client_exposes_stable_game_runtime(isolated_lane, unique_username):
    seed_player(unique_username, position=(188, 157), hit_points=69)

    try:
        async with browser_session() as (_, _, page):
            await login_seeded_player(
                page,
                unique_username,
                client_url=isolated_lane.client_url,
            )
            first = await page.evaluate(
                """() => {
                    const descriptor = Object.getOwnPropertyDescriptor(window, 'game');
                    const game = window.game;
                    window.__kaetramRuntimeReference = game;
                    return {
                        configurable: descriptor?.configurable,
                        writable: descriptor?.writable,
                        hasPlayer: Boolean(game?.player),
                        hasMap: Boolean(game?.map),
                        hasMenu: Boolean(game?.menu),
                        hasSocket: Boolean(game?.socket),
                        hasEntities: Boolean(game?.entities),
                    };
                }"""
            )

            assert first == {
                "configurable": False,
                "writable": False,
                "hasPlayer": True,
                "hasMap": True,
                "hasMenu": True,
                "hasSocket": True,
                "hasEntities": True,
            }

            await asyncio.sleep(1)
            assert await page.evaluate(
                "() => window.game === window.__kaetramRuntimeReference"
            )
    finally:
        cleanup_player(unique_username)
