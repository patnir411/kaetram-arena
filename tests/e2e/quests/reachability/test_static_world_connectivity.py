"""Static world-connectivity reachability tests.

These tests verify the **claims in `prompts/game_knowledge.md`** against
the offline `Kaetram-Open/packages/server/data/map/world.json` — no game
server, no browser, no MCP. They run in <1s and act as guardrails on
prompt-knowledge accuracy: when a Core 3 quest framework moves, or
`game_knowledge.md` adds a "canonical coord example", we want a fast
failing test, not a 6h agent run that BFS-loops.

Methodology
-----------
Two BFS layers over `world.json`:

  - **Walkable BFS** — pure tile-collision check. Answers "is this pixel
    walkable on the static map?"
  - **Region-graph BFS** — collapses the map into walkable components,
    then connects components via UNGATED door teleports (`destination`
    pairs without `reqAchievement` / `reqQuest`). Answers "can a vanilla
    agent reach this point using only navigate + traverse_door, with
    no seeded achievements / no finished prereq quests?"

Coverage
--------
Each Core 3 quest's agent-visible coords (NPC + canonical pin chain +
canonical resource coord) are checked for vanilla reachability from a
fresh Mudwich spawn. The Paprika `(298, 300)` regression test guards
against the prompt re-introducing a coord trapped in a disjoint pocket.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from tests.e2e.quests.reachability.conftest import reachability

WORLD_JSON_PATH = Path(
    "/home/patnir41/projects/Kaetram-Open/packages/server/data/map/world.json"
)


def _load_world() -> tuple[int, int, list, set, list, dict]:
    with WORLD_JSON_PATH.open() as f:
        w = json.load(f)
    width = w["width"]
    height = w["height"]
    data = w["data"]
    collisions = set(w["collisions"])
    doors = w["areas"]["doors"]
    by_id = {d["id"]: d for d in doors if d.get("id") is not None}
    return width, height, data, collisions, doors, by_id


_FLIP = 0x80000000 | 0x40000000 | 0x20000000


def _make_collider(width: int, height: int, data: list, collisions: set):
    def colliding(x: int, y: int) -> bool:
        if x < 0 or y < 0 or x >= width or y >= height:
            return True
        cell = data[width * y + x]
        if not cell:
            return True
        tiles = [cell] if isinstance(cell, int) else cell
        return any(
            ((t & ~_FLIP if (t & _FLIP) else t) in collisions) for t in tiles
        )

    return colliding


def _walkable_region(start: tuple[int, int], colliding) -> set[tuple[int, int]]:
    if colliding(*start):
        return set()
    seen = {start}
    q = deque([start])
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if n in seen or colliding(*n):
                continue
            seen.add(n)
            q.append(n)
    return seen


def _vanilla_reachable_regions(
    mud_start: tuple[int, int],
    doors: list,
    by_id: dict,
    colliding,
) -> tuple[set[tuple[int, int]], dict[tuple[int, int], tuple[int, int]]]:
    """Return the set of region representatives reachable from Mudwich
    via UNGATED doors only (no `reqAchievement` / `reqQuest`)."""
    landmarks: set[tuple[int, int]] = {mud_start}
    for d in doors:
        if d.get("x") is not None and not colliding(d["x"], d["y"]):
            landmarks.add((d["x"], d["y"]))
        partner = by_id.get(d.get("destination"))
        if partner and not colliding(partner["x"], partner["y"]):
            landmarks.add((partner["x"], partner["y"]))

    landmark_region: dict[tuple[int, int], tuple[int, int]] = {}
    for lm in landmarks:
        if lm in landmark_region:
            continue
        comp = _walkable_region(lm, colliding)
        for other in landmarks:
            if other in comp and other not in landmark_region:
                landmark_region[other] = lm

    mud_rep = landmark_region[mud_start]
    seen = {mud_rep}
    queue = deque([mud_rep])
    while queue:
        cur = queue.popleft()
        for d in doors:
            if d.get("reqAchievement") or d.get("quest") or d.get("reqQuest"):
                continue
            sx, sy = d.get("x"), d.get("y")
            if landmark_region.get((sx, sy)) != cur:
                continue
            partner = by_id.get(d.get("destination"))
            if not partner:
                continue
            tx, ty = partner["x"], partner["y"]
            dst_rep = landmark_region.get((tx, ty))
            if dst_rep is None or dst_rep in seen:
                continue
            seen.add(dst_rep)
            queue.append(dst_rep)
    return seen, landmark_region


@pytest.fixture(scope="module")
def world():
    width, height, data, collisions, doors, by_id = _load_world()
    colliding = _make_collider(width, height, data, collisions)
    mud_start = (188, 157)  # Mudwich spawn — game_knowledge.md MUDWICH_SPAWN
    mud_walkable = _walkable_region(mud_start, colliding)
    vanilla_regions, landmark_region = _vanilla_reachable_regions(
        mud_start, doors, by_id, colliding
    )

    def vanilla_reachable(point: tuple[int, int]) -> bool:
        # Direct walkable component check first (handles Mudwich-internal points).
        if point in mud_walkable:
            return True
        # Otherwise must be reachable via ungated door chain.
        rep = landmark_region.get(point)
        if rep is None:
            comp = _walkable_region(point, colliding)
            for lm in landmark_region:
                if lm in comp:
                    rep = lm
                    break
        return rep in vanilla_regions if rep else False

    return {
        "width": width,
        "height": height,
        "colliding": colliding,
        "mud_walkable": mud_walkable,
        "vanilla_regions": vanilla_regions,
        "landmark_region": landmark_region,
        "vanilla_reachable": vanilla_reachable,
    }


# ---------------------------------------------------------------------------
# Core 3 quests — game_knowledge.md asserts each is on the benchmark route.
# Each test below verifies the AGENT-VISIBLE coords cited in game_knowledge.md
# are actually reachable from a vanilla post-tutorial Mudwich state.
# ---------------------------------------------------------------------------


@reachability
def test_foresting_forester_reachable_vanilla(world):
    """Foresting NPC at (216, 114) — game_knowledge.md QUEST CATALOG row 1."""
    assert world["vanilla_reachable"]((216, 114)), (
        "Forester at (216, 114) must be vanilla-reachable from Mudwich. "
        "If this fails, Foresting is broken and the benchmark is unfounded."
    )


@reachability
def test_herbalist_npc_reachable_vanilla(world):
    """Herby Mc. Herb at (333, 281) — game_knowledge.md QUEST CATALOG row 2."""
    assert world["vanilla_reachable"]((333, 281)), (
        "Herbalist NPC at (333, 281) must be vanilla-reachable from Mudwich. "
        "Herbalist's Desperation is Core 3; the NPC has to be reachable."
    )


@reachability
def test_ricksroll_door_1025_reachable_vanilla(world):
    """Rick's Roll door 1025 at (379, 388) — game_knowledge.md KEY QUEST LOCATIONS.

    Verifies the canonical pin-chain endpoint (the door to Rick) is
    walkable from Mudwich via ungated tiles. Door 1025 teleports to
    (1138, 800) which is inside Rick's region.
    """
    assert world["vanilla_reachable"]((379, 388)), (
        "Door 1025 entry at (379, 388) must be vanilla-reachable from "
        "Mudwich. Without this, Rick's Roll is unreachable."
    )


@reachability
def test_ricksroll_pin_chain_all_reachable_vanilla(world):
    """Verify every pin coord in `game_knowledge.md`'s recommended
    Rick's Roll chain is walkable from Mudwich.

    Locks in that the pin chain itself is correct map-wise. If a coord
    here ever becomes vanilla-unreachable the prompt's chain advice is
    broken and agents will BFS-loop.
    """
    pins = [
        (245, 170), (285, 190), (293, 242), (311, 254),
        (324, 301), (340, 345), (367, 348), (375, 370), (379, 388),
    ]
    unreachable = [p for p in pins if not world["vanilla_reachable"](p)]
    assert not unreachable, (
        f"game_knowledge.md Rick's Roll pin chain has unreachable coords: "
        f"{unreachable}. Pin chain must be all-walkable from Mudwich. "
        f"Update the chain or the prompt example."
    )


@reachability
def test_paprika_recommended_coords_are_reachable(world):
    """Paprika Bush coords cited in `game_knowledge.md` RESOURCE LOCATIONS.

    The prompt recommends `(286, 326)` and `(305, 360)` — both confirmed
    walkable from Mudwich and close to the canonical Lakesworld warp
    landing `(319, 281)`. The disjoint-pocket coord `(298, 300)` is
    explicitly called out in the prompt as a bush to AVOID; if it ever
    becomes vanilla-reachable (e.g. the map gets re-cut) the warning
    can be relaxed.
    """
    recommended = [(286, 326), (305, 360)]
    unreachable = [p for p in recommended if not world["vanilla_reachable"](p)]
    assert not unreachable, (
        f"game_knowledge.md recommended Paprika coords are not "
        f"vanilla-reachable from Mudwich: {unreachable}. The map data "
        f"may have changed — re-pick from foraging.json paprikabush "
        f"placements."
    )
    # And the disjoint-pocket coord must STILL be in a disjoint pocket —
    # if this changes, agents won't suffer the multi-session BFS-loop
    # pattern observed historically and the prompt's warning can be
    # softened.
    assert not world["vanilla_reachable"]((298, 300)), (
        "(298, 300) is now vanilla-reachable from Mudwich. The map may "
        "have been re-cut; relax game_knowledge.md's warning about it."
    )


@reachability
def test_paprika_cluster_has_walkable_cells(world):
    """Sanity: the paprika cluster range (286-390, 240-484) from
    `game_knowledge.md` RESOURCE LOCATIONS has plenty of vanilla-reachable
    cells, so Herbalist's paprika step isn't broken — it's the example
    coord that has to be picked carefully."""
    cluster_walkable = sum(
        1 for x in range(286, 391, 4) for y in range(240, 485, 4)
        if (x, y) in world["mud_walkable"]
    )
    assert cluster_walkable >= 100, (
        f"Paprika cluster only has {cluster_walkable} reachable cells "
        f"from Mudwich (sampling every 4 tiles). Quest may be broken."
    )
