#!/usr/bin/env python3
"""Find a warp+door route from Mudwich to a target tile.

For each candidate warp landing (mudwich/aynor/lakesworld/patsow/crullfield/
undersea), do a BFS over walkable tiles + doors as edges, and report the
shortest route in terms of (warp + door hops).

Usage:
  scripts/analysis/find_warp_route.py 333 281        # Herbalist
  scripts/analysis/find_warp_route.py 1088 833       # Rick
  scripts/analysis/find_warp_route.py 293 729        # Water Guardian
  scripts/analysis/find_warp_route.py 52 310         # Sponge
"""
from __future__ import annotations
import json
import sys
from collections import deque
from pathlib import Path

WORLD_JSON = Path(__file__).resolve().parents[2].parent / "Kaetram-Open" / "packages" / "server" / "data" / "map" / "world.json"


def load_world():
    with WORLD_JSON.open() as f:
        return json.load(f)


def colliding(world, x, y):
    W = world["width"]; data = world["data"]; collisions = set(world["collisions"])
    idx = W * y + x
    if idx < 0 or idx >= len(data):
        return True
    d = data[idx]
    if not d:
        return True
    tiles = [d] if isinstance(d, int) else d
    FLIP = (0x80000000 | 0x40000000 | 0x20000000)
    return any(((t & ~FLIP if t & FLIP else t) in collisions) for t in tiles)


def label_regions(world):
    """Flood-fill walkable tiles into connected regions; return (region_of_tile, region_sizes)."""
    W, H = world["width"], world["height"]
    region: dict[tuple[int, int], int] = {}
    sizes: list[int] = []
    for y in range(H):
        for x in range(W):
            if (x, y) in region or colliding(world, x, y):
                continue
            rid = len(sizes)
            size = 0
            q = deque([(x, y)])
            region[(x, y)] = rid
            while q:
                cx, cy = q.popleft()
                size += 1
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    nx, ny = cx + dx, cy + dy
                    if (nx, ny) in region:
                        continue
                    if 0 <= nx < W and 0 <= ny < H and not colliding(world, nx, ny):
                        region[(nx, ny)] = rid
                        q.append((nx, ny))
            sizes.append(size)
    return region, sizes


def adj_regions(region, pos):
    x, y = pos
    seen = set()
    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        if (x + dx, y + dy) in region:
            seen.add(region[(x + dx, y + dy)])
    return seen


def build_door_graph(world, region):
    doors = world["areas"]["doors"]
    door_by_id = {d["id"]: d for d in doors}
    edges: dict[int, list[tuple[int, dict]]] = {}
    for d in doors:
        dest_id = d.get("destination")
        if dest_id is None:
            continue
        dd = door_by_id.get(dest_id)
        if not dd:
            continue
        src_regs = adj_regions(region, (d["x"], d["y"]))
        dst_regs = adj_regions(region, (dd["x"], dd["y"]))
        info = {
            "door_id": d["id"],
            "src_xy": (d["x"], d["y"]),
            "dst_xy": (dd["x"], dd["y"]),
            "level": d.get("level"),
            "skill": d.get("skill"),
            "quest": d.get("quest") or d.get("reqQuest"),
            "achievement": d.get("achievement") or d.get("reqAchievement"),
        }
        for sr in src_regs:
            for tr in dst_regs:
                edges.setdefault(sr, []).append((tr, info))
    return edges


def find_shortest_path(start_region, target_region, edges):
    """BFS from start_region to target_region. Returns list of (door_info)."""
    if start_region == target_region:
        return []
    visited = {start_region: None}
    q = deque([start_region])
    while q:
        r = q.popleft()
        if r == target_region:
            break
        for nr, info in edges.get(r, []):
            if nr in visited:
                continue
            visited[nr] = (r, info)
            q.append(nr)
    if target_region not in visited:
        return None
    path = []
    n = target_region
    while visited[n] is not None:
        prev, info = visited[n]
        path.append(info)
        n = prev
    path.reverse()
    return path


def find_route(world, region, edges, target_x, target_y):
    target = (target_x, target_y)
    if target not in region:
        # Snap to nearest walkable
        best = None; bd = 999
        for r in range(0, 12):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if (target[0] + dx, target[1] + dy) in region:
                        d = abs(dx) + abs(dy)
                        if d < bd:
                            bd = d
                            best = (target[0] + dx, target[1] + dy)
        if best is None:
            return None
        target = best
    target_region = region[target]

    warps = {
        "mudwich":    (188, 157),
        "aynor":      (411, 288),
        "lakesworld": (319, 281),
        "patsow":     (343, 127),
        "crullfield": (266, 158),
        "undersea":   (43, 313),
    }
    warp_quests = {
        "aynor":      "ancientlands",
        "lakesworld": "desertquest",
        "crullfield": "desertquest",
    }
    warp_achievements = {
        "patsow": "patsow",
        "undersea": "waterguardian",
    }

    best = None
    for wname, wpos in warps.items():
        if wpos not in region:
            continue
        wr = region[wpos]
        path = find_shortest_path(wr, target_region, edges)
        if path is None:
            continue
        candidate = {
            "warp": wname,
            "warp_landing": wpos,
            "warp_quest": warp_quests.get(wname),
            "warp_achievement": warp_achievements.get(wname),
            "doors": path,
            "door_count": len(path),
            "target": target,
            "target_region": target_region,
        }
        if best is None or candidate["door_count"] < best["door_count"]:
            best = candidate
    return best


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    target_x = int(argv[1])
    target_y = int(argv[2])
    world = load_world()
    region, sizes = label_regions(world)
    edges = build_door_graph(world, region)
    route = find_route(world, region, edges, target_x, target_y)
    if not route:
        print(f"NO ROUTE found from any warp to ({target_x},{target_y})")
        return 2
    print(f"Best route to (snapped) {route['target']} (region {route['target_region']}, size {sizes[route['target_region']]}):")
    print(f"  1. warp '{route['warp']}' -> lands at {route['warp_landing']}")
    if route['warp_quest']:
        print(f"     gate: quest='{route['warp_quest']}' (must be finished)")
    if route['warp_achievement']:
        print(f"     gate: achievement='{route['warp_achievement']}' (must be earned)")
    for i, d in enumerate(route['doors'], start=2):
        gates = [f"{k}={v}" for k, v in (("level", d["level"]), ("skill", d["skill"]), ("quest", d["quest"]), ("achievement", d["achievement"])) if v]
        gate_str = f" [{', '.join(gates)}]" if gates else ""
        print(f"  {i}. door {d['door_id']} step on {d['src_xy']} -> teleport to {d['dst_xy']}{gate_str}")
    print(f"  {len(route['doors']) + 2}. walk overland to {route['target']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
