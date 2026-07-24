from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.opd.factorial_power import (
    PowerContractError,
    paired_t_power,
    validate_power_record,
)


REPO = Path(__file__).resolve().parents[2]
POWER_RECORD = REPO / "research" / "experiments" / "opd-2b-factorial-power-v1.json"


def test_power_record_recomputes_exactly_with_pinned_engine() -> None:
    record = json.loads(POWER_RECORD.read_text())
    calculation = validate_power_record(record)

    assert calculation["minimum_replicates_at_target_power"] == 17
    assert calculation["power_at_16"] == pytest.approx(0.7938544220598713)
    assert calculation["power_at_17"] == pytest.approx(0.830990138886351)
    assert calculation["power_at_20"] == pytest.approx(0.9107747406019532)
    assert calculation["critical_t_at_20"] == pytest.approx(
        3.013624610310775,
        abs=1e-14,
    )


def test_power_record_rejects_alpha_or_calculation_drift() -> None:
    record = json.loads(POWER_RECORD.read_text())
    record["per_estimand_two_sided_alpha"] = 0.05
    with pytest.raises(PowerContractError, match="alpha allocation drifted"):
        validate_power_record(record)

    record = json.loads(POWER_RECORD.read_text())
    record["calculation"]["power_at_20"] = 0.99
    with pytest.raises(PowerContractError, match="calculation drifted"):
        validate_power_record(record)

    record = json.loads(POWER_RECORD.read_text())
    record["calculation_absolute_tolerance"] = 1e-3
    with pytest.raises(PowerContractError, match="tolerance drifted"):
        validate_power_record(record)

    record = json.loads(POWER_RECORD.read_text())
    record["power_model"] = "unspecified"
    with pytest.raises(PowerContractError, match="working model drifted"):
        validate_power_record(record)

    record = json.loads(POWER_RECORD.read_text())
    record["standardized_effect_definition"] = "sample d_z"
    with pytest.raises(PowerContractError, match="population effect definition drifted"):
        validate_power_record(record)


def test_paired_t_power_rejects_invalid_inputs() -> None:
    with pytest.raises(PowerContractError, match="at least two"):
        paired_t_power(1, standardized_effect=1, two_sided_alpha=0.05)
    with pytest.raises(PowerContractError, match="alpha"):
        paired_t_power(20, standardized_effect=1, two_sided_alpha=0)


def test_power_record_rejects_short_or_malformed_numeric_contract() -> None:
    record = json.loads(POWER_RECORD.read_text())
    record["planned_replicates"] = 17
    with pytest.raises(PowerContractError, match="20-replicate"):
        validate_power_record(record)

    record = json.loads(POWER_RECORD.read_text())
    record["standardized_effect"] = "not-a-number"
    with pytest.raises(PowerContractError, match="numeric definition"):
        validate_power_record(record)

    with pytest.raises(PowerContractError, match="root must be an object"):
        validate_power_record([1])  # type: ignore[arg-type]
