"""Fail-closed guard for preregistered held-out quest evaluations.

The held-out quest is evaluation-only.  OPD seed and teacher-grading paths call
this module before doing any state mutation or endpoint work so a future data
collection change cannot silently leak the task into training.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRATION = REPO_ROOT / "research" / "experiments" / "heldout-quest.json"
REQUIRED_FORBIDDEN_USES = frozenset({"training_seed", "teacher_grading"})


class HeldOutGuardError(ValueError):
    """Raised when a held-out registration or use violates preregistration."""


def normalize_quest(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


@dataclass(frozen=True)
class HeldOutRegistration:
    experiment_id: str
    quest_name: str
    quest_key: str
    aliases: tuple[str, ...]
    allowed_uses: frozenset[str]
    forbidden_uses: frozenset[str]
    path: Path

    @property
    def normalized_aliases(self) -> frozenset[str]:
        return frozenset(normalize_quest(v) for v in self.aliases if v)


def load_registration(path: str | Path = DEFAULT_REGISTRATION) -> HeldOutRegistration:
    registration_path = Path(path).resolve()
    try:
        raw = json.loads(registration_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HeldOutGuardError(f"cannot load held-out registration {registration_path}: {exc}") from exc

    if raw.get("schema_version") != 1:
        raise HeldOutGuardError("held-out registration schema_version must be 1")
    if raw.get("locked") is not True:
        raise HeldOutGuardError("held-out registration must have locked=true")
    if raw.get("preregistered_before_evaluation") is not True:
        raise HeldOutGuardError("held-out quest must be preregistered before evaluation")

    quest = raw.get("quest") or {}
    name = str(quest.get("name") or "").strip()
    key = str(quest.get("key") or "").strip()
    if not name or not key:
        raise HeldOutGuardError("held-out registration requires quest.name and quest.key")

    aliases = tuple(dict.fromkeys([name, key, *(quest.get("aliases") or [])]))
    allowed = frozenset(raw.get("allowed_uses") or [])
    forbidden = frozenset(raw.get("forbidden_uses") or [])
    if allowed != frozenset({"evaluation"}):
        raise HeldOutGuardError("held-out registration must allow evaluation only")
    if not REQUIRED_FORBIDDEN_USES.issubset(forbidden):
        missing = sorted(REQUIRED_FORBIDDEN_USES - forbidden)
        raise HeldOutGuardError(f"held-out registration missing forbidden uses: {missing}")

    return HeldOutRegistration(
        experiment_id=str(raw.get("experiment_id") or "").strip(),
        quest_name=name,
        quest_key=key,
        aliases=aliases,
        allowed_uses=allowed,
        forbidden_uses=forbidden,
        path=registration_path,
    )


def validate_eval_selection(
    requested_quest: str,
    path: str | Path = DEFAULT_REGISTRATION,
) -> HeldOutRegistration:
    registration = load_registration(path)
    if normalize_quest(requested_quest) not in registration.normalized_aliases:
        raise HeldOutGuardError(
            f"requested quest {requested_quest!r} does not match preregistered "
            f"quest {registration.quest_name!r}"
        )
    return registration


def assert_quests_not_reserved(
    quests: Iterable[str],
    *,
    use: str,
    path: str | Path = DEFAULT_REGISTRATION,
) -> None:
    registration = load_registration(path)
    if use not in registration.forbidden_uses:
        raise HeldOutGuardError(f"guard called for unregistered forbidden use {use!r}")
    conflicts = sorted({q for q in quests if normalize_quest(q) in registration.normalized_aliases})
    if conflicts:
        raise HeldOutGuardError(
            f"held-out quest leakage blocked for {use}: {', '.join(conflicts)} "
            f"is reserved by {registration.path}"
        )


def assert_text_not_reserved(
    text: str,
    *,
    use: str,
    source: str,
    path: str | Path = DEFAULT_REGISTRATION,
) -> None:
    registration = load_registration(path)
    if use not in registration.forbidden_uses:
        raise HeldOutGuardError(f"guard called for unregistered forbidden use {use!r}")
    normalized_text = normalize_quest(text)
    matches = sorted(alias for alias in registration.normalized_aliases if alias in normalized_text)
    if matches:
        raise HeldOutGuardError(
            f"held-out quest leakage blocked for {use} in {source}: "
            f"matched {registration.quest_name!r}"
        )
