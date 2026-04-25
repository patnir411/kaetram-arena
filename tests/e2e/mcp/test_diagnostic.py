"""Ad-hoc diagnostics. Delete once the foundation is green."""

from __future__ import annotations

import asyncio
import pytest

from tests.e2e.helpers.browser import browser_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


async def test_diag_click_login_and_watch(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(199, 169),
    )
    try:
        async with browser_session() as (_b, _c, page):
            console_log = []
            page.on("console", lambda msg: console_log.append(f"[{msg.type}] {msg.text}"))
            page.on("pageerror", lambda err: console_log.append(f"[pageerror] {err}"))

            await page.goto(isolated_lane.client_url, wait_until="domcontentloaded")
            await page.wait_for_function(
                "() => { const b = document.querySelector('#login'); return !!b && !b.disabled; }",
                timeout=30_000,
            )

            await page.locator("#login-name-input").fill(unique_username)
            await page.locator("#login-password-input").fill("test")
            await page.locator("#login").click()
            print(f"LOGIN_CLICKED as user={unique_username!r}")

            for i in range(20):
                state = await page.evaluate(
                    """() => ({
                        bodyClass: document.body.className,
                        errorText: (document.querySelector('.error') || {}).textContent,
                        validationText: (document.querySelector('.validation') || {}).textContent,
                        statusText: (document.querySelector('.status') || {}).textContent,
                        loginDisabled: (document.querySelector('#login') || {}).disabled,
                        hasGame: !!window.game,
                        connected: !!(window.game && window.game.connection && window.game.connection.connected),
                        playerName: window.game && window.game.player && window.game.player.name,
                        gridX: window.game && window.game.player && window.game.player.gridX,
                    })"""
                )
                print(f"t={i}s:", state)
                if state.get("bodyClass") == "game":
                    print("REACHED GAME!")
                    break
                await asyncio.sleep(1)

            print("\n--- CONSOLE LOG ---")
            for line in console_log[-50:]:
                print(line)
    finally:
        cleanup_player(unique_username)
