"""Load inline JS scripts from standalone files.

These scripts were extracted from mcp_game_server.py to enable proper syntax
highlighting, linting, and unit testing.  Each constant holds the full JS source
as a string ready for ``page.evaluate(JS_CONSTANT, ...)``.
"""

from pathlib import Path

_JS_DIR = Path(__file__).parent


def _load(name: str) -> str:
    return (_JS_DIR / name).read_text()


OBSERVE_SCRIPT = _load("observe.js")
SHOP_UI_STATE = _load("shop_ui_state.js")
NUDGE_STORE = _load("nudge_store.js")
BUY_PACKET = _load("buy_packet.js")
INVENTORY_SNAPSHOT = _load("inventory_snapshot.js")
