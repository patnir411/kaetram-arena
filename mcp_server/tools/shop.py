"""Shop tools: buy_item."""

import json

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, log, log_tool, mcp
from mcp_server.helpers import inventory_snapshot_with_gold
from mcp_server.js import BUY_PACKET, SHOP_UI_STATE
from mcp_server.tools.npc import _resolve_shop_interaction
from mcp_server.utils import NPC_STORE_KEYS, compact_shop_ui


@mcp.tool()
async def buy_item(ctx: Context, npc_name: str, item_index: int, count: int = 1) -> str:
    """Buy an item from an NPC's shop.

    Args:
        npc_name: Store NPC name (e.g. 'Forester', 'Miner', 'Babushka', 'Clerk')
        item_index: Index of item in the shop (0-based)
        count: Number to buy (default 1)
    """
    log_tool("buy_item", args={"npc_name": npc_name, "item_index": item_index, "count": count})
    page = await get_page(ctx)
    before = await inventory_snapshot_with_gold(page)
    if isinstance(before, dict) and before.get("error"):
        return json.dumps(before)

    log(f"[buy_item] npc={npc_name} idx={item_index} cnt={count} gold={before.get('gold','?')}")

    store_key = NPC_STORE_KEYS.get(npc_name.lower())
    if not store_key:
        return json.dumps({"error": f"Unknown store NPC '{npc_name}'. Known: {', '.join(NPC_STORE_KEYS.keys())}", "npc": npc_name})

    interaction = await _resolve_shop_interaction(page, npc_name, store_key=store_key)
    ci = dict(interaction) if isinstance(interaction, dict) else interaction
    if isinstance(ci, dict) and "ui" in ci:
        ci["ui"] = compact_shop_ui(ci.get("ui"))
    log(f"[buy_item] shop_interaction={ci}")

    ui_state = interaction.get("ui") if isinstance(interaction, dict) else {}
    store_state = ui_state.get("shop") if isinstance(ui_state, dict) else {}
    if not (isinstance(interaction, dict) and interaction.get("matched_expectation")):
        return json.dumps({"bought": False, "store": store_key, "item_index": item_index,
            "error": interaction.get("error", "Store interaction failed") if isinstance(interaction, dict) else "Store interaction failed",
            "interaction": interaction, "store_state": store_state})

    await page.wait_for_timeout(1000)
    buy_result = await page.evaluate(BUY_PACKET, [store_key, item_index, count])
    if isinstance(buy_result, dict) and buy_result.get("error"):
        return json.dumps(buy_result)
    log(f"[buy_item] buy_packet_result={buy_result}")
    await page.wait_for_timeout(2500)

    after = await inventory_snapshot_with_gold(page)
    store_state_after = await page.evaluate(SHOP_UI_STATE)
    log(f"[buy_item] after_gold={after.get('gold','?')} post_buy_ui={compact_shop_ui(store_state_after)}")

    before_items = before.get("items", {}) if isinstance(before, dict) else {}
    after_items = after.get("items", {}) if isinstance(after, dict) else {}
    gained = {k: v - before_items.get(k, 0) for k, v in after_items.items() if v - before_items.get(k, 0) > 0 and k != "gold"}
    gold_before = before.get("gold", 0) if isinstance(before, dict) else 0
    gold_after = after.get("gold", 0) if isinstance(after, dict) else 0
    spent = gold_before - gold_after

    if gained:
        log(f"[buy_item] success gained={gained} spent={spent}")
        return json.dumps({"bought": True, "store": store_key, "items_gained": gained,
            "gold_spent": spent, "gold_remaining": gold_after, "interaction": interaction})
    else:
        log(f"[buy_item] failed no diff store_key={store_key} idx={item_index}")
        return json.dumps({"bought": False, "store": store_key, "item_index": item_index,
            "error": "Purchase may have failed — no new items in inventory.",
            "gold_before": gold_before, "gold_after": gold_after,
            "interaction": interaction, "post_ui": store_state_after})
