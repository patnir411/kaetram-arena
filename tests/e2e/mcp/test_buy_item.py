"""buy_item() — buy a real item from a real current-tree shopkeeper."""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player, snapshot_player

from ..helpers.mcp_client import mcp_session

AUTOSAVE_WAIT = 5.0
# Miner is a confirmed store NPC on the current tree and is easy to seed
# adjacent to deterministically.
MINER_POS = (323, 179)
START_GOLD = 500


@pytest.mark.mcp
async def test_buy_coal_from_miner_shopkeeper(test_username):
    """Seed enough gold adjacent to Miner and buy Coal (item index 0).

    Important caveat: the Miner NPC cannot open the shop while he is occupied
    by Miner's Quest / Miner's Quest II dialogue. This test therefore seeds a
    player with those Miner quests in a completed state and asserts that
    precondition from live observe() before attempting the purchase.
    """
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=MINER_POS,
        inventory=[
            {"index": 0, "key": "gold", "count": START_GOLD},
        ],
        quests=[
            {"key": "minersquest", "stage": 2, "subStage": 0, "completedSubStages": []},
            {"key": "minersquest2", "stage": 3, "subStage": 0, "completedSubStages": []},
        ],
    )
    try:
        seeded = snapshot_player(test_username)
        seeded_slots = (seeded.get("player_inventory") or {}).get("slots") or []
        seeded_gold = next(
            (int(slot.get("count", 0)) for slot in seeded_slots if slot.get("key") == "gold"),
            0,
        )
        assert seeded_gold >= START_GOLD, (
            f"seed did not persist gold to Mongo (expected {START_GOLD}, got {seeded_gold}); "
            f"slots={seeded_slots}"
        )

        async with mcp_session(username=test_username) as s:
            obs = (await s.call_tool("observe", {})).json() or {}
            gold_before = next(
                (int(i.get("count", 0)) for i in (obs.get("inventory") or [])
                 if str(i.get("name", "")).lower() == "gold"),
                0,
            )
            assert gold_before >= 50, (
                f"expected >=50 gold in observe, got {gold_before}; "
                f"mongo_seed_gold={seeded_gold}; observe_inventory={obs.get('inventory')}"
            )
            active_quests = [str(q.get("name", "")).lower() for q in (obs.get("active_quests") or [])]
            assert "miner's quest" not in active_quests, f"Miner shop blocked by active quest state: {active_quests}"
            assert "miner's quest ii" not in active_quests, f"Miner shop blocked by active quest state: {active_quests}"
            finished_quests = [str(q.get("name", "")).lower() for q in (obs.get("finished_quests") or [])]
            assert "miner's quest" in finished_quests, f"expected Miner's Quest to be finished, got: {finished_quests}"
            assert "miner's quest ii" in finished_quests, f"expected Miner's Quest II to be finished, got: {finished_quests}"

            # Deliberate settle time so headed dashboard runs are watchable and
            # the live client has a moment to finish initial UI hydration.
            await asyncio.sleep(1.5)
            res = await s.call_tool("buy_item", {"npc_name": "Miner", "item_index": 0, "count": 1})
            assert not res.is_error, f"buy_item errored: {res.text[:300]}"
            data = res.json() or {}
            await asyncio.sleep(1.0)
            assert data.get("bought") is True, f"expected successful purchase, got: {data}"
            gained = data.get("items_gained") or {}
            assert int(gained.get("coal", 0)) >= 1, f"expected coal in purchase delta, got: {data}"
            assert int(data.get("gold_spent", 0)) >= 50, f"expected coal cost to be spent, got: {data}"

        snap = snapshot_player(test_username)
        inv_slots = (snap.get("player_inventory") or {}).get("slots") or []
        inv_keys = [sl.get("key") for sl in inv_slots if sl.get("key")]
        gold_after = next(
            (int(sl.get("count", 0)) for sl in inv_slots if sl.get("key") == "gold"),
            0,
        )
        assert gold_after < START_GOLD, (
            f"gold unchanged in Mongo (before={START_GOLD}, after={gold_after}); inv={inv_keys}"
        )
        assert "coal" in inv_keys, f"coal missing from Mongo inventory after purchase; inv={inv_keys}"
    finally:
        cleanup_player(test_username)
