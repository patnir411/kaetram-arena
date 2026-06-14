"""Offset-tracking JSONL tail helper.

Session logs grow into the hundreds of MB during long runs, and mtime changes
every few seconds during play. With 5 tabs hitting the agents endpoint every
8 s, re-reading the whole file (or its last 1 MB) on each mtime change would be
tens of full reparses per minute.

This module gives every parser an O(new_bytes) path: persist (offset, accumulator)
per file, on each call seek past the offset, parse only complete new lines, and
hand the parser its prior accumulator to merge into. Modeled on the same pattern
used in `mcp_server/state_heartbeat.py:activity_heartbeat_loop`.

Usage from a parser:

    from dashboard._log_tail import tail_new_lines

    state_cache = {}  # filepath -> {"offset": int, "last_size": int, "acc": Any}

    def parse(filepath):
        slot = state_cache.get(filepath)
        if slot is None:
            slot = {"offset": 0, "last_size": -1, "acc": init_accumulator()}
            state_cache[filepath] = slot
        for obj in tail_new_lines(filepath, slot):
            consume(slot["acc"], obj)
        return finalize(slot["acc"])
"""

import json
import os
from collections import OrderedDict


def tail_new_lines(filepath: str, slot: dict):
    """Yield parsed JSON objects appended since the last call.

    `slot` is a mutable dict the caller persists per-filepath. It must contain
    `offset` (int) and `last_size` (int). On rotation/truncation (size < offset)
    we reset the offset; the caller is responsible for resetting `acc` too — we
    signal this by setting `slot["rotated"] = True` for the duration of the call.

    Only complete lines are processed: bytes after the last newline are left
    unread until the next call. This mirrors `state_heartbeat.activity_heartbeat_loop`
    so we never see half-written JSON.
    """
    slot["rotated"] = False
    try:
        size = os.path.getsize(filepath)
    except OSError:
        return
    if size < slot.get("offset", 0):
        slot["offset"] = 0
        slot["rotated"] = True
        slot["acc"] = None  # caller must rebuild
    if size == slot.get("last_size", -1):
        return
    slot["last_size"] = size
    if slot["acc"] is None:
        return  # caller must rebuild before next call

    try:
        with open(filepath, "rb") as f:
            f.seek(slot["offset"])
            chunk = f.read(size - slot["offset"])
    except OSError:
        return

    last_nl = chunk.rfind(b"\n")
    if last_nl == -1:
        return  # no complete line yet
    processable = chunk[: last_nl + 1]
    slot["offset"] += len(processable)

    for line in processable.decode("utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


class IncrementalCache:
    """LRU cache of (slot, finalized_result) per filepath, keyed by filepath.

    `slot` carries the offset/last_size/acc state across calls; `finalized_result`
    is what the parser returns to its caller. We snapshot the finalized result so
    repeat calls without new bytes don't re-finalize (cheap operation, but worth
    avoiding when ten tabs hit /api/agents simultaneously).
    """

    def __init__(self, max_entries: int = 25):
        self._slots: OrderedDict[str, dict] = OrderedDict()
        self._max = max_entries

    def get_slot(self, filepath: str, init_fn) -> dict:
        slot = self._slots.get(filepath)
        if slot is None:
            slot = {
                "offset": 0,
                "last_size": -1,
                "acc": init_fn(),
                "result": None,
            }
            self._slots[filepath] = slot
        else:
            self._slots.move_to_end(filepath)
        while len(self._slots) > self._max:
            self._slots.popitem(last=False)
        return slot

    def reset_slot(self, slot: dict, init_fn) -> None:
        slot["offset"] = 0
        slot["last_size"] = -1
        slot["acc"] = init_fn()
        slot["result"] = None

    def evict(self, filepath: str) -> None:
        self._slots.pop(filepath, None)
