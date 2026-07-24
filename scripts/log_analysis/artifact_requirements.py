"""Fail-closed checks for log-derived research claims.

Research scripts must never turn an absent run bundle into a zero-valued
observation.  These helpers validate the expected agent/run/session layout
before an analysis starts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


class MissingEvidenceError(RuntimeError):
    """Raised when a claimed analysis input is absent or incomplete."""


SEMANTIC = "semantic"
TERMINAL_ONLY_INTERRUPTED = "terminal_only_interrupted"
INVALID = "invalid"


def _read_session_records(path: Path) -> list[dict] | None:
    try:
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError):
        return None

    records: list[dict] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(record, dict):
            return None
        records.append(record)
    return records


def classify_session_evidence(path: Path) -> str:
    """Classify a session without treating model narration as world evidence.

    A terminal-only interrupted log is intentionally narrow: exactly one
    initialization record followed by one clean interrupted session-end
    record.  It can be disclosed and excluded from an otherwise semantic run
    bundle.  Any other non-semantic shape is invalid and keeps the bundle
    fail-closed.
    """
    records = _read_session_records(path)
    if not records:
        return INVALID

    saw_model = False
    saw_environment = False
    for record in records:
        record_type = record.get("type")
        payload = record.get("message", record.get("content"))
        if record_type in {"assistant", "raw_model_emission"} and payload not in (
            None,
            "",
            [],
            {},
        ):
            saw_model = True
        if record_type in {"user", "tool_result", "game_state"} and payload not in (None, "", [], {}):
            saw_environment = True
    if len(records) >= 2 and saw_model and saw_environment:
        return SEMANTIC

    if len(records) == 2:
        init, terminal = records
        if (
            init.get("type") == "system"
            and init.get("subtype") == "init"
            and terminal.get("type") == "result"
            and terminal.get("subtype") == "session_end"
            and terminal.get("result") == "interrupted"
            and terminal.get("terminal_reason") == "interrupted"
            and terminal.get("is_error") is False
        ):
            return TERMINAL_ONLY_INTERRUPTED

    return INVALID


def has_semantic_session_evidence(path: Path) -> bool:
    """Require a well-formed model/environment interaction."""
    return classify_session_evidence(path) == SEMANTIC


def audit_agent_run_logs(
    raw_root: str | Path,
    *,
    agents: Iterable[str],
    run_ids: Iterable[str],
) -> dict[str, list[str]]:
    """Audit run bundles and report exclusions separately from invalid logs."""
    root = Path(raw_root)
    missing: list[str] = []
    excluded_terminal_only: list[str] = []
    invalid_sessions: list[str] = []
    for agent in sorted(set(agents)):
        for run_id in sorted(set(run_ids)):
            run_dir = root / agent / "runs" / run_id
            if not run_dir.is_dir():
                missing.append(str(run_dir))
                continue

            logs = sorted(run_dir.glob("session_*.log"))
            if not logs:
                missing.append(str(run_dir / "session_*.log"))
                continue

            semantic_count = 0
            run_invalid: list[str] = []
            for path in logs:
                if not path.is_file():
                    run_invalid.append(str(path))
                    continue
                classification = classify_session_evidence(path)
                if classification == SEMANTIC:
                    semantic_count += 1
                elif classification == TERMINAL_ONLY_INTERRUPTED:
                    excluded_terminal_only.append(str(path))
                else:
                    run_invalid.append(str(path))

            invalid_sessions.extend(run_invalid)
            if semantic_count == 0 or run_invalid:
                missing.append(str(run_dir / "session_*.log"))

    return {
        "missing": missing,
        "excluded_terminal_only": excluded_terminal_only,
        "invalid_sessions": invalid_sessions,
    }


def missing_agent_run_logs(
    raw_root: str | Path,
    *,
    agents: Iterable[str],
    run_ids: Iterable[str],
) -> list[str]:
    """Return missing run directories or session-log globs in stable order."""
    return audit_agent_run_logs(
        raw_root, agents=agents, run_ids=run_ids,
    )["missing"]


def require_agent_run_logs(
    raw_root: str | Path,
    *,
    agents: Iterable[str],
    run_ids: Iterable[str],
    analysis: str,
) -> None:
    """Require every declared agent/run bundle before computing statistics."""
    missing = missing_agent_run_logs(raw_root, agents=agents, run_ids=run_ids)
    if not missing:
        return
    preview = "\n".join(f"  - {path}" for path in missing[:20])
    remainder = len(missing) - min(len(missing), 20)
    suffix = f"\n  - ... and {remainder} more" if remainder else ""
    raise MissingEvidenceError(
        f"{analysis} blocked: {len(missing)} required raw-log bundle(s) are missing.\n"
        f"Restore the immutable artifacts before reporting statistics:\n"
        f"{preview}{suffix}"
    )


def require_files(paths: Iterable[str | Path], *, analysis: str) -> None:
    """Require non-empty supporting artifacts such as a training dataset."""
    missing = [str(Path(path)) for path in paths if not Path(path).is_file() or Path(path).stat().st_size == 0]
    if missing:
        rendered = "\n".join(f"  - {path}" for path in sorted(missing))
        raise MissingEvidenceError(
            f"{analysis} blocked: required supporting artifact(s) are missing or empty:\n"
            f"{rendered}"
        )
