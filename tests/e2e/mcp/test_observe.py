"""observe() — returns expected shape, position, inventory, equipment, and ASCII map."""

from __future__ import annotations

import pytest

from ..helpers.mcp_client import mcp_session


@pytest.mark.mcp
async def test_observe_unified_shape(seeded_player):
    """Observe returns the unified view with all expected top-level keys,
    seeded position near Mudwich (188,157), seeded Bronze Axe in inventory
    with item key, equipment block, ASCII map, and STUCK_CHECK trailer."""
    async with mcp_session(username=seeded_player["username"]) as s:
        res = await s.call_tool("observe", {})
        assert not res.is_error, f"unexpected error: {res.text[:200]}"
        data = res.json()
        assert data is not None, "could not parse JSON from observe"

        # Core structure
        for key in ("pos", "stats", "equipment", "skills", "status",
                    "nearby", "inventory", "active_quests", "finished_quests"):
            assert key in data, f"missing key '{key}'"

        # Nearby is categorized
        nearby = data.get("nearby") or {}
        for cat in ("npcs", "mobs", "resources", "ground_items"):
            assert cat in nearby, f"missing nearby.{cat}"

        # Status block
        status = data.get("status") or {}
        for field in ("dead", "stuck", "nav", "indoors"):
            assert field in status, f"missing status.{field}"

        # ASCII map IS present in unified mode
        assert "\n\nASCII_MAP:" in res.text, "missing ASCII_MAP"
        assert "\n\nSTUCK_CHECK:" in res.text, "missing STUCK_CHECK trailer"

        # Position check
        pos = data.get("pos") or {}
        assert 180 <= pos.get("x", 0) <= 200, f"unexpected x: {pos}"
        assert 150 <= pos.get("y", 0) <= 170, f"unexpected y: {pos}"

        # Inventory has item keys
        inv = data.get("inventory") or []
        assert any(i.get("key") == "bronzeaxe" for i in inv), (
            f"bronzeaxe missing from inventory keys: {inv}"
        )
        # Inventory items have names too (backward compat)
        inv_names = [i.get("name", "").lower() for i in inv]
        assert any("axe" in n for n in inv_names), f"axe missing from names: {inv_names}"
