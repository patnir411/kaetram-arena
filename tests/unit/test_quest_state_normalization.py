"""Tests for the query_quest state-shape contract.

`query_quest` reads `window.__latestGameState`, which carries a FLAT `quests`
array (what `__extractGameState()` returns), NOT the `active_quests`/
`finished_quests` split that `observe` exposes. `current_step`/`live_gate_status`
must derive the split (via `normalize_quest_lists`) or an accepted quest reads as
un-accepted. These tests drive the REAL raw shape, not a hand-fed normalized dict.
"""

from mcp_server.tools.quest import _build_current_step
from mcp_server.utils import load_quest_walkthroughs, normalize_quest_lists


def _herbalists() -> tuple[str, dict]:
    for key, quest in load_quest_walkthroughs().items():
        if isinstance(quest, dict) and "herbalist" in (quest.get("name") or key).lower():
            return quest.get("name") or key, quest
    raise AssertionError("Herbalist's Desperation entry not found")


# Raw __latestGameState shape: flat `quests`, with started/finished/stage/stageCount.
def _raw_state(quests, inventory=None):
    return {"quests": quests, "inventory": inventory or []}


def test_normalize_from_raw_quests_shape():
    raw = _raw_state([
        {"name": "Foresting", "stage": 3, "stageCount": 3, "started": True, "finished": True},
        {"name": "Herbalist's Desperation", "stage": 1, "stageCount": 3,
         "started": True, "finished": False},
    ])
    active, finished = normalize_quest_lists(raw)
    assert [q["name"] for q in active] == ["Herbalist's Desperation"]
    assert active[0]["stage"] == 1
    assert [q["name"] for q in finished] == ["Foresting"]


def test_normalize_passthrough_when_already_split():
    # observe-shaped state (already split) must pass through unchanged.
    state = {"active_quests": [{"name": "X", "stage": 2}], "finished_quests": [{"name": "Y"}]}
    active, finished = normalize_quest_lists(state)
    assert active == [{"name": "X", "stage": 2}]
    assert finished == [{"name": "Y"}]


def test_current_step_accepted_through_normalization():
    """The fix: raw shape → normalize → current_step reports accepted, not false."""
    name, quest = _herbalists()
    raw = _raw_state(
        [{"name": name, "stage": 1, "stageCount": 3, "started": True, "finished": False}],
        inventory=[{"key": "bluelily", "count": 1}],
    )
    active, finished = normalize_quest_lists(raw)
    live = {**raw, "active_quests": active, "finished_quests": finished}
    step = _build_current_step(quest, name, live)
    assert step.get("accepted") is True
    assert step.get("stage") == 1
    assert step["remaining"] == {"bluelily": 2}


def test_raw_shape_without_normalization_reads_unaccepted():
    """The flat raw shape fed directly to `_build_current_step` reads as
    accepted:false — which is why `query_quest` must normalize the split first."""
    name, quest = _herbalists()
    raw = _raw_state(
        [{"name": name, "stage": 1, "stageCount": 3, "started": True, "finished": False}],
        inventory=[{"key": "bluelily", "count": 1}],
    )
    broken = _build_current_step(quest, name, raw)  # no active_quests key present
    assert broken.get("accepted") is False  # unaccepted without the normalize step
    # ...and normalization fixes it:
    active, finished = normalize_quest_lists(raw)
    fixed = _build_current_step(quest, name, {**raw, "active_quests": active, "finished_quests": finished})
    assert fixed.get("accepted") is True


def test_current_step_finished_through_normalization():
    name, quest = _herbalists()
    raw = _raw_state(
        [{"name": name, "stage": 3, "stageCount": 3, "started": True, "finished": True}])
    active, finished = normalize_quest_lists(raw)
    live = {**raw, "active_quests": active, "finished_quests": finished}
    step = _build_current_step(quest, name, live)
    assert step.get("finished") is True


def test_current_step_not_accepted_when_absent():
    name, quest = _herbalists()
    raw = _raw_state([])  # no quests started
    active, finished = normalize_quest_lists(raw)
    live = {**raw, "active_quests": active, "finished_quests": finished}
    step = _build_current_step(quest, name, live)
    assert step.get("accepted") is False
