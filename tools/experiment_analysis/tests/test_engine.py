"""Analytic counterexamples and adversarial input checks, without Isaac."""

from copy import deepcopy
import math
import random

import pytest

from tools.experiment_analysis import ANALYSIS_VERSION
from tools.experiment_analysis.engine import analyze
from tools.experiment_analysis.planning import readiness
from tools.experiment_analysis.schema import AnalysisError, Dataset, Design, digest, read_json
from tools.experiment_analysis.synthetic import generate


def fixture(values, *, kind="mean", cells=None, repeats=3, floor=0):
    """values[cell][base][repeat] -> one vector per repeat, no result shortcuts."""
    artifact = digest("synthetic_test")
    all_cells, units, rows = [], [], []
    terms_count = len(values[0][0][0])
    for c, bases in enumerate(values):
        cid = f"c{c}"
        all_cells.append(dict(id=cid, domain="synthetic", primary_label="P2", difficulty="fixed", min_instances=1, min_coverage=1))
        for b, observations in enumerate(bases):
            uid = f"{cid}/u{b}"
            units.append(dict(id=uid, cell_id=cid, members={"main": uid}, repeats=[f"r{i}" for i in range(len(observations))],
                              supports=["support"], infrastructure_status="valid"))
            for r, vector in enumerate(observations):
                for t, value in enumerate(vector):
                    rows.append(dict(unit_id=uid, base_instance_id=uid, member="main", system_id="system", condition_id=f"z{t}",
                                     branch_role="fact", checkpoint_round="e0", repeat_id=f"r{r}", status="valid", method_outcome="completed",
                                     values={"outcome": value}, artifact_digest=artifact, pair_context_digest=digest([uid, r])))
    terms = [dict(member="main", condition_id=f"z{i}", branch_role="fact", checkpoint_round="e0", measure="outcome") for i in range(terms_count)]
    est = dict(id="endpoint", kind=kind, cells=cells or [c["id"] for c in all_cells], support_id="support", terms=terms,
               min_repeats=repeats, min_units=1, min_coverage=1)
    if "ratio" in kind:
        est["denominator_floor"] = floor
    if kind == "linear":
        est["coefficients"] = [1, -1] if terms_count == 2 else [1, -1, -1, 1]
    design = dict(schema=ANALYSIS_VERSION + ".design", id="test", authority="test", protocol_digest=digest("test"), systems=["system"],
                  cells=all_cells, units=units, measures=[dict(id="outcome", kind="scalar", unit="unit")], estimands=[est],
                  bootstrap=dict(seed=9, resamples=80))
    data = dict(schema=ANALYSIS_VERSION + ".dataset", design_digest=digest(design), provenance=dict(kind="synthetic", producer="test",
                source_revision="v1", evidence_digests=[artifact]), records=rows)
    return design, data


def rebind(design, data):
    data["design_digest"] = digest(design)


def result(design, data):
    return analyze(design, data)["results"][0]


def test_macro_not_pooled_epochs_or_cells():
    # 2 bases in c0: means 1,0 => .5; 1 base in c1: 0 =>0.
    # Equal cell result=.25, pooled repeats=5/11, pooled bases=1/3.
    d, r = fixture([[[[1]] * 5, [[0]] * 3], [[[0]] * 3]])
    actual = result(d, r)
    assert actual["estimate"] == .25
    assert actual["coverage"]["c0"]["aligned_repeats"] == 8
    assert actual["interval"]["reason"] == "insufficient_independent_clusters"


def test_ratio_panel_means_differs_from_mean_of_ratios():
    d, r = fixture([[[[1, 1]] * 3], [[[0, .5]] * 3]], kind="ratio_of_means", floor=.5)
    assert result(d, r)["estimate"] == pytest.approx(2 / 3)
    d["estimands"][0]["kind"] = "mean_of_cell_ratios"
    rebind(d, r)
    assert result(d, r)["estimate"] == .5


def test_zero_opportunity_stays_in_panel():
    d, r = fixture([[[[1, 1]] * 3], [[[0, 0]] * 3]], kind="ratio_of_means")
    assert result(d, r)["estimate"] == 1
    d["estimands"][0]["kind"] = "mean_of_cell_ratios"
    rebind(d, r)
    assert result(d, r)["status"] == "N/A"


def test_missing_required_cell_suppresses_whole_panel():
    d, r = fixture([[[[1]] * 3], [[[0]] * 3]])
    r["records"] = [row for row in r["records"] if row["unit_id"].startswith("c0/")]
    actual = result(d, r)
    assert actual["estimate"] is None
    assert "endpoint_coverage:c1" in actual["reasons"]
    assert actual["coverage"]["c1"]["support_planned"] == 1
    assert len(actual["units"][1]["missing"]) == 3


def test_pair_requires_three_aligned_repeat_ids_not_three_in_each_arm():
    d, r = fixture([[[[1, 0]] * 4]], kind="linear")
    r["records"] = [row for row in r["records"] if (row["condition_id"], row["repeat_id"]) not in {("z0", "r0"), ("z1", "r3")}]
    actual = result(d, r)
    assert actual["units"][0]["aligned_repeats"] == 2
    assert actual["estimate"] is None


def test_pair_context_mismatch_does_not_remove_fact_from_unpaired_mean():
    d, r = fixture([[[[1, 0]] * 3]], kind="linear")
    r["records"][1]["pair_context_digest"] = digest("different")
    assert result(d, r)["estimate"] is None
    d["estimands"][0].update(kind="mean", terms=d["estimands"][0]["terms"][:1])
    del d["estimands"][0]["coefficients"]
    rebind(d, r)
    assert result(d, r)["estimate"] == 1


def test_method_failure_remains_outcome_and_cannot_be_missing():
    d, r = fixture([[[[1]] * 3, [[0]] * 3]])
    for row in r["records"][3:]:
        row["method_outcome"] = "timeout"
    assert result(d, r)["estimate"] == .5
    r["records"][3].update(status="not_evaluable", values={}, reason="timeout")
    with pytest.raises(AnalysisError, match="method failures"):
        result(d, r)


def test_repeat_duplication_does_not_create_new_clusters_or_narrow_ci():
    d, r = fixture([[[[1]] * 3, [[0]] * 3]])
    original = result(d, r)
    extra = deepcopy(r["records"])
    for row in extra:
        row["repeat_id"] += "copy"
        row["pair_context_digest"] = digest([row["unit_id"], row["repeat_id"]])
    r["records"] += extra
    for unit in d["units"]:
        unit["repeats"] += [s + "copy" for s in unit["repeats"]]
    rebind(d, r)
    repeated = result(d, r)
    assert repeated["estimate"] == original["estimate"]
    assert repeated["interval"] == original["interval"]
    assert repeated["interval"]["independent_clusters"] == 2


def test_factor_interaction_four_way_common_support():
    d, r = fixture([[[[1, .7, .8, .4]] * 3, [[1, .7, .8, .4]] * 3]], kind="linear")
    actual = result(d, r)
    assert actual["estimate"] == pytest.approx(-.1)
    r["records"].pop()
    assert result(d, r)["estimate"] is None


def test_undefined_ratio_bootstrap_not_silently_dropped():
    d, r = fixture([[[[1, 1]] * 3, [[0, 0]] * 3]], kind="ratio_of_means")
    actual = result(d, r)
    assert actual["estimate"] == 1
    assert actual["interval"]["status"] == "N/A"
    assert actual["interval"]["undefined_resamples"] > 0


def test_order_invariance_for_bootstrap_and_point_results():
    d, r = fixture([[[[1]] * 3, [[.2]] * 3, [[.7]] * 3], [[[.9]] * 3, [[.1]] * 3]])
    original = analyze(d, r)["results"]
    random.Random(6).shuffle(r["records"])
    d["units"].reverse()
    d["cells"].reverse()
    rebind(d, r)
    assert analyze(d, r)["results"] == original


def test_infrastructure_coverage_does_not_shrink_with_missing_data():
    d, r = fixture([[[[1]] * 3, [[0]] * 3]])
    d["units"][1].update(infrastructure_status="unverified", reason="oracle_unverified")
    rebind(d, r)
    actual = result(d, r)
    assert actual["coverage"]["c0"]["fraction"] == .5
    assert "infrastructure_coverage:c0" in actual["reasons"]


@pytest.mark.parametrize("mutation", [
    lambda d, r: r["records"].append(deepcopy(r["records"][0])),
    lambda d, r: r["records"][0].update(repeat_id="unplanned"),
    lambda d, r: r["records"][0].update(base_instance_id="wrong_base"),
    lambda d, r: r["records"][0].update(artifact_digest="f" * 64),
    lambda d, r: r["records"][0]["values"].update(outcome=float("nan")),
    lambda d, r: r["records"][0]["values"].update(outcome=True),
    lambda d, r: r["records"][0].update(attempt=1),
    lambda d, r: r["provenance"].update(kind="measured"),
    lambda d, r: r["provenance"].update(publishable=True),
    lambda d, r: d["bootstrap"].update(seed=42),
])
def test_invalid_or_tampered_input_rejected(mutation):
    d, r = fixture([[[[1]] * 3]])
    mutation(d, r)
    with pytest.raises((AnalysisError, ValueError)):
        analyze(d, r)


def test_duplicate_base_cannot_be_split_into_repeat_units():
    d, r = fixture([[[[1]] * 3]])
    duplicate = deepcopy(d["units"][0])
    duplicate["id"] = "new_id"
    d["units"].append(duplicate)
    with pytest.raises(AnalysisError, match="one base instance"):
        Design.parse(d)


def test_strict_json_duplicate_and_nonfinite(tmp_path):
    path = tmp_path / "input.json"
    for value in ('{"a":1,"a":2}', '{"a":NaN}'):
        path.write_text(value)
        with pytest.raises(AnalysisError):
            read_json(path)


def test_readiness_deduplicates_reused_continuations():
    d, r = fixture([[[[1]] * 3, [[0]] * 3]])
    duplicate = deepcopy(d["estimands"][0])
    duplicate["id"] = "another_endpoint"
    d["estimands"].append(duplicate)
    report = readiness(d)
    assert report["required_continuations"] == 6
    d["estimands"][0]["min_repeats"] = 4
    assert readiness(d)["status"] == "design_insufficient"


def test_end_to_end_analytic_controls_include_unequal_family_sizes():
    d, data, expected = generate(resamples=20)
    report = analyze(d, data)
    assert report["qualification"] == "synthetic_validation"
    assert report["publishable"] is False
    assert len(report["results"]) == 19
    plan = readiness(d)
    assert "independent_base_instances" not in plan
    assert plan["registered_member_instances"] == 344
    assert plan["inference_clusters_by_estimand"]["control_transfer"] == {"unit": "family", "planned_count": 3}
    for row in report["results"]:
        value = expected[row["estimand_id"]]
        if isinstance(value, dict):
            value = value[row["system_id"]]
        assert row["estimate"] == pytest.approx(value), row["estimand_id"]
        assert row["publishable"] is False
        if row["kind"] == "family_gap":
            assert row["interval"]["independent_clusters"] == 3
            assert [v["valid_pairs"] for v in row["family_coverage"].values()] == [40, 44, 48]
    # A missing required family cannot be reweighted away.
    data["records"] = [r for r in data["records"] if not r["unit_id"].startswith("family2/")]
    missing = [r for r in analyze(d, data)["results"] if r["kind"] == "family_gap"]
    assert all(r["estimate"] is None for r in missing)


def test_event_endpoint_uses_competing_risk_not_binary_or_km():
    # Four clusters, each triplicated: resume at 2; fail at 1; censor at 3;
    # resume at 4. AJ resume CIF = 1/4 + (1/2)*(1/1) = 3/4.
    d, r = fixture([[[[{"time": time, "cause": cause}]] * 3 for time, cause in
                       [(2, "resume"), (1, "fail"), (3, "censor"), (4, "resume")]]])
    d["measures"][0] = dict(id="outcome", kind="event", unit="tick", causes=["resume", "fail"])
    d["estimands"][0].update(kind="cif", cause="resume", horizon=4)
    rebind(d, r)
    actual = result(d, r)
    assert actual["estimate"] == .75
    assert actual["components"]["cells"]["c0"]["risk"] == 12
    assert actual["interval"]["cluster"] == "base_instance"


def test_family_panel_still_requires_every_declared_cell():
    d, r, _ = generate(resamples=2)
    est = next(e for e in d["estimands"] if e["kind"] == "family_gap")
    est["cells"].append("control_a")
    d["estimands"] = [est]
    rebind(d, r)
    assert readiness(d)["status"] == "design_insufficient"
    assert all(row["estimate"] is None and "endpoint_coverage:control_a" in row["reasons"] for row in analyze(d, r)["results"])


def test_linear_contrast_rejects_incompatible_units():
    d, r = fixture([[[[1, 2]] * 3]], kind="linear")
    d["measures"].append(dict(id="seconds", kind="scalar", unit="s"))
    d["estimands"][0]["terms"][1]["measure"] = "seconds"
    with pytest.raises(AnalysisError, match="same natural unit"):
        Design.parse(d)
