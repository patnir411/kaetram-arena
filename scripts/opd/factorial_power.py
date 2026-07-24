#!/usr/bin/env python3
"""Recompute and validate the prospective paired-t working-model power record."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import scipy
from scipy.stats import nct, t


class PowerContractError(ValueError):
    """The frozen power record does not match its executable calculation."""


def paired_t_power(
    n_replicates: int,
    *,
    standardized_effect: float,
    two_sided_alpha: float,
) -> tuple[float, float]:
    if n_replicates < 2:
        raise PowerContractError("paired-t power requires at least two replicates")
    if not math.isfinite(standardized_effect):
        raise PowerContractError("standardized effect must be finite")
    if not 0 < two_sided_alpha < 1:
        raise PowerContractError("two-sided alpha must be in (0,1)")
    degrees_of_freedom = n_replicates - 1
    critical = float(t.ppf(1.0 - two_sided_alpha / 2.0, degrees_of_freedom))
    noncentrality = standardized_effect * math.sqrt(n_replicates)
    power = float(
        nct.cdf(-critical, degrees_of_freedom, noncentrality)
        + nct.sf(critical, degrees_of_freedom, noncentrality)
    )
    return critical, power


def expected_calculation(record: dict[str, Any]) -> dict[str, Any]:
    familywise_alpha = float(record["familywise_alpha"])
    family_size = len(record["primary_estimands"])
    standardized_effect = float(record["standardized_effect"])
    per_estimand_alpha = familywise_alpha / family_size
    planned_replicates = int(record["planned_replicates"])
    if planned_replicates < 20:
        raise PowerContractError(
            "power record must cover the frozen 20-replicate design"
        )

    powers: dict[int, tuple[float, float]] = {}
    minimum = None
    for n_replicates in range(2, planned_replicates + 1):
        powers[n_replicates] = paired_t_power(
            n_replicates,
            standardized_effect=standardized_effect,
            two_sided_alpha=per_estimand_alpha,
        )
        if minimum is None and powers[n_replicates][1] >= float(record["target_power"]):
            minimum = n_replicates
    if minimum is None:
        raise PowerContractError("planned sample never reaches target power")

    return {
        "engine": f"scipy=={scipy.__version__}",
        "distribution": "scipy.stats.nct",
        "minimum_replicates_at_target_power": minimum,
        "power_at_16": powers[16][1],
        "power_at_17": powers[17][1],
        "power_at_20": powers[20][1],
        "critical_t_at_20": powers[20][0],
    }


def validate_power_record(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise PowerContractError("power-record root must be an object")
    estimands = record.get("primary_estimands")
    familywise_alpha = record.get("familywise_alpha")
    if (
        not isinstance(estimands, list)
        or not estimands
        or isinstance(familywise_alpha, bool)
        or not isinstance(familywise_alpha, (int, float))
    ):
        raise PowerContractError("power-record family definition is invalid")
    if record.get("per_estimand_two_sided_alpha") != (
        familywise_alpha / len(estimands)
    ):
        raise PowerContractError("power-record alpha allocation drifted")
    if record.get("power_model") != (
        "independent_normally_distributed_paired_differences"
    ):
        raise PowerContractError("power-record working model drifted")
    if record.get("standardized_effect_definition") != (
        "delta_over_sigma_D=population_mean(paired cluster differences)"
        "/population_SD(paired cluster differences)"
    ):
        raise PowerContractError("power-record population effect definition drifted")
    try:
        expected = expected_calculation(record)
    except PowerContractError:
        raise
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise PowerContractError(f"power-record numeric definition is invalid: {exc}") from exc
    tolerance = record.get("calculation_absolute_tolerance")
    if tolerance != 5e-15:
        raise PowerContractError("power-record calculation tolerance drifted")
    actual = record.get("calculation")
    if not isinstance(actual, dict):
        raise PowerContractError("power-record calculation must be an object")
    exact_keys = (
        "engine",
        "distribution",
        "minimum_replicates_at_target_power",
    )
    numeric_keys = (
        "power_at_16",
        "power_at_17",
        "power_at_20",
        "critical_t_at_20",
    )
    exact_drift = any(actual.get(key) != expected[key] for key in exact_keys)
    numeric_drift = False
    for key in numeric_keys:
        value = actual.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            numeric_drift = True
            break
        if not math.isclose(
            float(value),
            float(expected[key]),
            rel_tol=0.0,
            abs_tol=tolerance,
        ):
            numeric_drift = True
            break
    if set(actual) != set(expected) or exact_drift or numeric_drift:
        raise PowerContractError(
            f"power-record calculation drifted: expected={expected}, "
            f"actual={actual}"
        )
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("power_record", type=Path)
    args = parser.parse_args()
    try:
        record = json.loads(args.power_record.read_text(encoding="utf-8"))
        calculation = validate_power_record(record)
    except (
        OSError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        ZeroDivisionError,
        OverflowError,
        PowerContractError,
    ) as exc:
        parser.error(str(exc))
    print(json.dumps(calculation, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
