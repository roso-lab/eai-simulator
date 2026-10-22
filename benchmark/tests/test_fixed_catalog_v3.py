from dataclasses import replace
import pytest
from benchmark.fixed_catalog_v3 import d5_catalog, FixedCatalogV3
from benchmark.analysis_v3 import ScheduledRunV3, analyze_dimension


def fixture(units):
    directory = {"f": {f"c{i}": [f"u{j}" for j in range(units)] for i in range(30)}}
    checkpoints = {(f,c,u): "checkpoint" for f,cases in directory.items() for c,us in cases.items() for u in us}
    catalog = d5_catalog(directory, checkpoints)
    runs = [ScheduledRunV3(e.run_id, e.instance_id, e.stratum, e.repeat_id, e.checkpoint_digest, "evidence", (float(int(e.case_id[1:])%2),), weight=e.weight) for e in catalog.entries]
    return catalog, runs


def test_correlated_unit_splitting_does_not_narrow_d5_interval():
    one, rows1 = fixture(1)
    ten, rows10 = fixture(10)
    first = analyze_dimension("D5", rows1, stratum_weights={"f":1}, fixed_catalog=one)
    second = analyze_dimension("D5", rows10, stratum_weights={"f":1}, fixed_catalog=ten)
    assert first["interval"] == pytest.approx(second["interval"])
    assert first["independent_units"] == second["independent_units"] == 90
    assert second["scheduled_attempts"] == 900


def test_frozen_catalog_rejects_deletion_and_reweighting():
    catalog, rows = fixture(2)
    with pytest.raises(ValueError, match="exactly match"):
        analyze_dimension("D5", rows[:-1], stratum_weights={"f":1}, fixed_catalog=catalog)
    changed = [replace(r, weight=r.weight*2) for r in rows]
    with pytest.raises(ValueError, match="weight"):
        analyze_dimension("D5", changed, stratum_weights={"f":1}, fixed_catalog=catalog)
    with pytest.raises(ValueError, match="frozen catalog"):
        analyze_dimension("D5", rows, stratum_weights={"f":1})


def test_d5_registration_rejects_attempt_level_independence():
    catalog, _ = fixture(2)
    with pytest.raises(ValueError, match="grouped"):
        FixedCatalogV3("D5", tuple(replace(e, independence_group=e.run_id) for e in catalog.entries))


def test_fixed_paired_interval_uses_complete_repeat_difference_range():
    catalog, rows = fixture(2)
    paired = catalog.paired_interval(rows, rows)
    point = catalog.analyze(rows)
    assert paired.point == 0
    assert paired.upper-paired.lower == pytest.approx(2*(point["interval"][1]-point["interval"][0]))


def test_d6_rejects_renormalized_single_run_and_missing_repeats():
    catalog, _ = fixture(1)
    entry = replace(catalog.entries[0], weight=1)
    with pytest.raises(ValueError, match="three registered repeats"):
        FixedCatalogV3("D6", (entry,))


def test_d2_residual_unknown_keeps_singleton_score_and_undefined_completion_status():
    rows = [ScheduledRunV3(str(i), "a", "cell", str(i), "cp", "evidence", (None,1,0,0), status="infrastructure_unknown") for i in range(3)]
    result = analyze_dimension("D2", rows, stratum_weights={"cell":1}, resamples=100)
    assert result["status"] == "valid"
    assert "endpoints" in result
    assert result["identification"][0] == result["identification"][1]
    assert result["undefined_completion_possible"] is True


def test_d5_catalog_excludes_and_records_below_floor_family():
    directory = {
        "ready": {f"c{i}": ["u"] for i in range(30)},
        "short": {"c0": ["u"]},
    }
    checkpoints = {(f, c, u): "checkpoint" for f, cases in directory.items() for c, units in cases.items() for u in units}
    catalog = d5_catalog(directory, checkpoints)
    assert catalog.excluded_families == ("short",)
    assert {entry.family_id for entry in catalog.entries} == {"ready"}
    one_case = tuple(replace(entry, weight=1/3) for entry in catalog.entries if entry.case_id == "c0")
    with pytest.raises(ValueError, match="at least 30"):
        FixedCatalogV3("D5", one_case)
