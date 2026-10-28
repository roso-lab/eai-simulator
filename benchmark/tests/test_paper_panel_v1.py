import copy
import math

import pytest

from benchmark.paper_panel_v1 import (
    PANEL_SCHEMA,
    PanelAggregationError,
    aggregate_d3_relation_panels,
    aggregate_fixed_panel,
    fixed_catalog_hoeffding_half_width,
    validate_panel_directory,
    _extract_panel_value,
)


def _directory(*, endpoint="demo", values=(0.2, 0.4, 0.6), cell1_value=0.8, unknown=False, min_valid_instances=1):
    records = []
    for base, value in zip(("a", "b"), values[:2]):
        for repeat in ("r1", "r2", "r3"):
            status = "unknown" if unknown and base == "b" and repeat == "r3" else "valid"
            row = {"base_instance_id": base, "repeat_id": repeat, "status": status}
            if status == "valid":
                row["value"] = value
            records.append(row)
    return {
        "schema": PANEL_SCHEMA,
        "endpoint": endpoint,
        "repeat_ids": ["r1", "r2", "r3"],
        "cells": {
            "c0": {
                "weight": 0.5,
                "planned_instances": 2,
                "infrastructure_valid_instances": ["a", "b"],
                "min_valid_instances": min_valid_instances,
                "min_coverage_fraction": 0.8,
                "records": records,
            },
            "c1": {
                "weight": 0.5,
                "planned_instances": 2,
                "infrastructure_valid_instances": ["a", "b"],
                "min_valid_instances": min_valid_instances,
                "min_coverage_fraction": 0.8,
                "records": [
                    {"base_instance_id": base, "repeat_id": repeat, "status": "valid", "value": cell1_value}
                    for base in ("a", "b") for repeat in ("r1", "r2", "r3")
                ],
            },
        },
    }


def test_fixed_panel_averages_repeats_then_instances_then_fixed_cells():
    result = aggregate_fixed_panel(_directory())
    assert result["status"] == "valid"
    # c0=(.2+.4)/2, c1=.8, then equal fixed cells.
    assert result["point"] == pytest.approx(0.55)
    assert result["lower"] == pytest.approx(0.55)
    assert result["upper"] == pytest.approx(0.55)
    assert result["unknown_instance_count"] == 0


def test_unknown_records_produce_identification_range_without_reweighting():
    result = aggregate_fixed_panel(_directory(unknown=True))
    assert result["status"] == "range"
    assert result["point"] is None
    assert result["lower"] < result["upper"]
    assert result["unknown_instance_count"] == 1
    assert sum(row["weight"] for row in result["cells"]) == pytest.approx(1.0)


def test_coverage_failure_is_incomplete_and_does_not_reweight_remaining_cells():
    directory = _directory(min_valid_instances=3)
    result = aggregate_fixed_panel(directory)
    assert result["status"] == "incomplete"
    assert result["point"] is None
    assert result["reason"] == "coverage_floor"


def test_d3_p3_subtypes_share_one_half_weight():
    dirs = {key: _directory(endpoint=key, values=(value, value), cell1_value=value) for key, value in {
        "p2": 0.2, "p3j": 0.4, "p3s": 0.6, "p4": 0.8,
    }.items()}
    result = aggregate_d3_relation_panels(dirs)
    expected = 100.0 / 3.0 * (0.2 + (0.4 + 0.6) / 2.0 + 0.8)
    assert result["status"] == "valid"
    assert result["point"] == pytest.approx(expected)


def test_validation_rejects_duplicate_or_unregistered_records_and_bad_weights():
    directory = _directory()
    bad = copy.deepcopy(directory)
    bad["cells"]["c0"]["records"].append(bad["cells"]["c0"]["records"][0])
    with pytest.raises(PanelAggregationError, match="duplicate"):
        validate_panel_directory(bad)
    bad = copy.deepcopy(directory)
    bad["cells"]["c1"]["weight"] = 0.4
    with pytest.raises(PanelAggregationError, match="sum to one"):
        validate_panel_directory(bad)


def test_fixed_catalog_hoeffding_is_paper_scale_and_validates_weights():
    width = fixed_catalog_hoeffding_half_width([0.25, 0.25, 0.25, 0.25])
    assert width == pytest.approx(100.0 * math.sqrt(math.log(40.0) / 8.0))
    with pytest.raises(PanelAggregationError, match="sum to one"):
        fixed_catalog_hoeffding_half_width([0.5])


@pytest.mark.parametrize("relation,subtype", [("P2", None), ("P3", "J"), ("P3", "S"), ("P4", None)])
@pytest.mark.parametrize("outcome,expected", [
    ("completed", ("valid", 1.0)), ("violated", ("valid", 0.0)),
    ("unreached", ("valid", 0.0)), ("unknown", ("unknown", None)),
    ("incomplete", ("unknown", None)),
])
def test_obligation_disposition_reaches_fixed_panel_without_changing_estimand(relation, subtype, outcome, expected):
    obligation = {"relation": relation, "outcome": outcome}
    path = "facts.obligations:" + relation
    if subtype is not None:
        obligation["subtype"] = subtype
        path += ":" + subtype
    status, value = _extract_panel_value({"facts": {"obligations": [obligation]}}, path)
    assert (status, value) == expected
    directory = _directory(values=(1.0, 1.0), cell1_value=1.0)
    record = directory["cells"]["c0"]["records"][0]
    record["status"] = status
    if value is None:
        record.pop("value")
    else:
        record["value"] = value
    result = aggregate_fixed_panel(directory)
    if status == "unknown":
        assert result["status"] == "range"
        assert result["lower"] == pytest.approx(11 / 12)
        assert result["upper"] == 1.0
    else:
        assert result["status"] == "valid"
        assert result["point"] == pytest.approx(1.0 if value else 11 / 12)
