"""Fail-closed guard for preregistered held-out quest evaluations.

The held-out quest is evaluation-only.  OPD seed and teacher-grading paths call
this module before doing any state mutation or endpoint work so a future data
collection change cannot silently leak the task into training.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent
LEGACY_REGISTRATION = REPO_ROOT / "research" / "experiments" / "heldout-quest.json"
DEFAULT_REGISTRATION = (
    REPO_ROOT / "research" / "experiments" / "heldout-quest-v2.json"
)
REQUIRED_FORBIDDEN_USES = frozenset({"training_seed", "teacher_grading"})


class HeldOutGuardError(ValueError):
    """Raised when a held-out registration or use violates preregistration."""


def normalize_quest(value: str) -> str:
    compatible = unicodedata.normalize("NFKC", value)
    return re.sub(r"[^a-z0-9]", "", compatible.casefold())


@dataclass(frozen=True)
class HeldOutRegistration:
    schema_version: int
    experiment_id: str
    quest_name: str
    quest_key: str
    aliases: tuple[str, ...]
    prompt_forbidden_markers: tuple[str, ...]
    training_exclusion_terms: tuple[str, ...]
    tokenizer_sha256: str
    snapshot_lock_sha256: str
    tokenizer_vocab_size: int
    forbidden_token_sequences: tuple[tuple[int, ...], ...]
    allowed_uses: frozenset[str]
    forbidden_uses: frozenset[str]
    path: Path
    content_sha256: str

    @property
    def normalized_aliases(self) -> frozenset[str]:
        return frozenset(normalize_quest(v) for v in self.aliases if v)

    @property
    def normalized_training_exclusions(self) -> frozenset[str]:
        return frozenset(
            normalize_quest(value)
            for value in self.training_exclusion_terms
            if value
        )

    @property
    def sha256(self) -> str:
        return self.content_sha256


def load_registration(path: str | Path = DEFAULT_REGISTRATION) -> HeldOutRegistration:
    registration_path = Path(path).resolve()
    try:
        registration_bytes = registration_path.read_bytes()
        raw = json.loads(registration_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeldOutGuardError(f"cannot load held-out registration {registration_path}: {exc}") from exc

    schema_version = raw.get("schema_version") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or schema_version not in {1, 2}:
        raise HeldOutGuardError(
            "held-out registration must be a JSON object with schema_version 1 or 2"
        )
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

    prompt_markers: tuple[str, ...] = ()
    training_terms = aliases
    tokenizer_sha256 = ""
    snapshot_lock_sha256 = ""
    forbidden_sequences: tuple[tuple[int, ...], ...] = ()
    if schema_version == 2:
        supersedes = raw.get("supersedes")
        if not isinstance(supersedes, dict) or set(supersedes) != {
            "path", "sha256", "reason"
        }:
            raise HeldOutGuardError("v2 registration requires an exact supersedes record")
        registration_repo_root = (
            registration_path.parents[2]
            if registration_path.parent.name == "experiments"
            and registration_path.parent.parent.name == "research"
            else REPO_ROOT
        )
        legacy_path = (registration_repo_root / str(supersedes["path"])).resolve()
        try:
            legacy_path.relative_to(registration_repo_root)
        except ValueError as exc:
            raise HeldOutGuardError("superseded registration must be inside the repository") from exc
        legacy_sha = str(supersedes["sha256"])
        if (
            not legacy_path.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", legacy_sha)
            or hashlib.sha256(legacy_path.read_bytes()).hexdigest() != legacy_sha
        ):
            raise HeldOutGuardError("superseded registration digest mismatch")

        prompt_guard = raw.get("prompt_guard")
        if not isinstance(prompt_guard, dict) or set(prompt_guard) != {
            "forbidden_markers_outside_objective"
        }:
            raise HeldOutGuardError("v2 registration requires an exact prompt_guard")
        markers = prompt_guard["forbidden_markers_outside_objective"]
        if (
            not isinstance(markers, list)
            or not markers
            or not all(isinstance(value, str) and value.strip() for value in markers)
            or len(set(markers)) != len(markers)
        ):
            raise HeldOutGuardError("prompt forbidden markers must be unique strings")
        prompt_markers = tuple(markers)

        training_guard = raw.get("training_guard")
        if not isinstance(training_guard, dict) or set(training_guard) != {
            "exclusion_terms", "tokenizer", "forbidden_token_sequences"
        }:
            raise HeldOutGuardError("v2 registration requires an exact training_guard")
        terms = training_guard["exclusion_terms"]
        if not isinstance(terms, list) or tuple(terms) != aliases:
            raise HeldOutGuardError(
                "training exclusion terms must exactly match every registered quest alias"
            )
        training_terms = tuple(terms)

        tokenizer = training_guard["tokenizer"]
        if not isinstance(tokenizer, dict) or set(tokenizer) != {
            "snapshot",
            "tokenizer_sha256",
            "snapshot_lock_sha256",
            "fix_mistral_regex",
            "vocab_size",
        }:
            raise HeldOutGuardError("v2 training tokenizer identity is malformed")
        tokenizer_sha256 = str(tokenizer["tokenizer_sha256"])
        snapshot_lock_sha256 = str(tokenizer["snapshot_lock_sha256"])
        tokenizer_vocab_size = tokenizer["vocab_size"]
        if (
            tokenizer.get("snapshot") != "base_2b"
            or tokenizer.get("fix_mistral_regex") is not False
            or not re.fullmatch(r"[0-9a-f]{64}", tokenizer_sha256)
            or not re.fullmatch(r"[0-9a-f]{64}", snapshot_lock_sha256)
            or not isinstance(tokenizer_vocab_size, int)
            or isinstance(tokenizer_vocab_size, bool)
            or tokenizer_vocab_size < 1
        ):
            raise HeldOutGuardError("v2 training tokenizer identity is invalid")

        sequence_records = training_guard["forbidden_token_sequences"]
        if (
            not isinstance(sequence_records, list)
            or [record.get("term") if isinstance(record, dict) else None
                for record in sequence_records] != list(training_terms)
        ):
            raise HeldOutGuardError(
                "forbidden token records must cover every exclusion term in order"
            )
        flattened: list[tuple[int, ...]] = []
        for record in sequence_records:
            if set(record) != {"term", "variants"} or not isinstance(
                record["variants"], list
            ) or not record["variants"]:
                raise HeldOutGuardError("forbidden token record is malformed")
            for variant in record["variants"]:
                token_ids = variant.get("token_ids") if isinstance(variant, dict) else None
                text = variant.get("text") if isinstance(variant, dict) else None
                if (
                    not isinstance(variant, dict)
                    or set(variant) != {"text", "token_ids"}
                    or not isinstance(text, str)
                    or normalize_quest(text) != normalize_quest(record["term"])
                    or not isinstance(token_ids, list)
                    or not token_ids
                    or not all(
                        isinstance(token, int)
                        and not isinstance(token, bool)
                        and token >= 0
                        and token < tokenizer_vocab_size
                        for token in token_ids
                    )
                ):
                    raise HeldOutGuardError("forbidden token variant is malformed")
                flattened.append(tuple(token_ids))
        if len(set(flattened)) != len(flattened):
            raise HeldOutGuardError("forbidden token sequences must be unique")
        forbidden_sequences = tuple(flattened)

    return HeldOutRegistration(
        schema_version=schema_version,
        experiment_id=str(raw.get("experiment_id") or "").strip(),
        quest_name=name,
        quest_key=key,
        aliases=aliases,
        prompt_forbidden_markers=prompt_markers,
        training_exclusion_terms=training_terms,
        tokenizer_sha256=tokenizer_sha256,
        snapshot_lock_sha256=snapshot_lock_sha256,
        tokenizer_vocab_size=tokenizer_vocab_size if schema_version == 2 else 0,
        forbidden_token_sequences=forbidden_sequences,
        allowed_uses=allowed,
        forbidden_uses=forbidden,
        path=registration_path,
        content_sha256=hashlib.sha256(registration_bytes).hexdigest(),
    )


def validate_eval_selection(
    requested_quest: str,
    path: str | Path = DEFAULT_REGISTRATION,
) -> HeldOutRegistration:
    registration = load_registration(path)
    if registration.schema_version != 2:
        raise HeldOutGuardError(
            "future held-out evaluation requires a schema_version 2 registration"
        )
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
    matches = sorted(
        alias
        for alias in registration.normalized_training_exclusions
        if alias in normalized_text
    )
    if matches:
        raise HeldOutGuardError(
            f"held-out quest leakage blocked for {use} in {source}: "
            f"matched {registration.quest_name!r} (via aliases: {', '.join(matches)})"
        )


def assert_prompt_not_reserved(
    prompt_without_objective: str,
    *,
    registration: HeldOutRegistration,
    source: str,
) -> None:
    """Reject held-out identifiers or hints anywhere outside the objective block."""
    markers = tuple(dict.fromkeys(
        [*registration.aliases, *registration.prompt_forbidden_markers]
    ))
    normalized_prompt = normalize_quest(prompt_without_objective)
    matches = sorted(
        marker
        for marker in markers
        if normalize_quest(marker) in normalized_prompt
    )
    if matches:
        raise HeldOutGuardError(
            f"held-out prompt leakage blocked in {source}: {matches}"
        )
