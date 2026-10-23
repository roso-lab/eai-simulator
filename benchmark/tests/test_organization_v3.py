from dataclasses import asdict, replace
import json

import pytest

from benchmark.organization_v3 import (
    OrganizationCheckV3 as Check, OrganizationInstanceV3 as Spec,
    OrganizationEventV3 as Event, OrganizationCheckObservationV3 as Fact,
    OrganizationRunV3 as Run, evaluate_organization_run, organization_report,
    organization_report_from_payload,
    organization_support_errors,
)


def spec(i="x", cell="c", feasibility="deployable", checks=None):
    return Spec(i, cell, feasibility, "certificate", "contract", "physical", 10, 20,
                tuple(checks or (Check("cap", "capability", ("a",)),)))


def event(kind="commit", tick=5, order=0, **kw):
    return Event(f"{kind}-{tick}-{order}", tick, order, kind, "evidence",
                 **({"plan_digest": "plan", "parsable": True} | kw if kind == "commit" else kw))


def run(s, repeat="1", *, passes=True, events=None, facts=None, infra=True):
    if events is None:
        events = ((event(),) if s.feasibility == "deployable" else
                  (event("declaration", declaration="not_deployable"),
                   event("safe_stop", tick=6, safe_stop_verified=True)))
    if facts is None:
        facts = tuple(Fact(c.check_id, a, passes, "fact") for c in s.checks for a in c.alternatives)
    return Run(s.instance_id, repeat, "system", s.digest(), "evidence", infra, 20,
               tuple(events), tuple(facts), "plan", "physical")


def test_applicable_categories_equal_weight_not_check_count():
    s = spec(checks=(Check("c1", "capability", ("a",)),
                     Check("c2", "capability", ("a",)),
                     Check("r", "role", ("a", "b"))))
    facts = (Fact("c1", "a", True, "f"), Fact("c2", "a", False, "f"),
             Fact("r", "b", True, "f"))  # one passing legal alternative suffices
    result = evaluate_organization_run(s, run(s, facts=facts))
    assert result["V"] == 0
    assert result["Qorg"] == .75
    assert result["category_shares"]["role"] == .5
    assert result["category_shares"]["capability"] == .25
    assert result["category_shares"]["deadline"] == 0


def test_internal_candidate_and_replanning_never_replace_first_commit():
    s = spec()
    events = (event("candidate", tick=1), event(tick=4, parsable=False), event(tick=6))
    result = evaluate_organization_run(s, run(s, events=events))
    assert result["first_commit_id"] == "commit-4-0"
    assert result["V"] == result["Qorg"] == 0
    r = run(s, events=(event(tick=4, plan_digest="first"), event(tick=6)))
    with pytest.raises(ValueError, match="FIRST"):
        evaluate_organization_run(s, r)
    r = replace(r, checked_plan_digest="first", checks=(Fact("cap", "a", False, "f"),))
    assert evaluate_organization_run(s, r)["Qorg"] == 0


@pytest.mark.parametrize("events,expected", [((), 0), ((event(tick=11),), 0), ((event(tick=10),), 1)])
def test_submission_deadline(events, expected):
    s = spec()
    assert evaluate_organization_run(s, run(s, events=events))["V"] == expected


def test_omission_requires_evaluator_false_missing_log_is_unknown():
    s = spec()
    assert evaluate_organization_run(s, run(s, facts=()))["status"] == "unknown"
    assert evaluate_organization_run(s, run(s, passes=False))["Qorg"] == 0
    with pytest.raises(ValueError, match="unregistered"):
        evaluate_organization_run(s, run(s, facts=(Fact("invented", "a", True, "f"),)))
    with pytest.raises(ValueError, match="duplicated"):
        run(s, facts=(Fact("cap", "a", True, "f"), Fact("cap", "a", True, "f")))


def test_public_physical_model_and_frozen_instance_binding():
    s = spec()
    with pytest.raises(ValueError, match="physical model"):
        evaluate_organization_run(s, replace(run(s), checked_physical_model_digest="planner_duration"))
    with pytest.raises(ValueError, match="frozen"):
        evaluate_organization_run(replace(s, submission_deadline_tick=9), run(s))


@pytest.mark.parametrize("extra,expected", [
    ((), 1), ((event("dispatch", tick=7),), 0),
    ((event("declaration", tick=19, declaration="malformed"),), 0),
    ((event("declaration", tick=20, declaration="deployable"),), 0),
])
def test_rejection_uses_declaration_in_force_and_no_dispatch(extra, expected):
    s = spec(feasibility="team_state_infeasible")
    r = run(s)
    assert evaluate_organization_run(s, replace(r, events=r.events+extra))["I"] == expected


def test_absent_declaration_and_unverified_safety_are_not_rejection():
    s = spec(feasibility="team_state_infeasible")
    for events in ((event("safe_stop", safe_stop_verified=True),),
                   (event("declaration", declaration="not_deployable"),)):
        assert evaluate_organization_run(s, run(s, events=events))["I"] == 0


def dataset(n=30, cells=("c",), unknown=0):
    specs, runs = [], []
    for cell in cells:
        for feasibility in ("deployable", "team_state_infeasible"):
            for i in range(n):
                s = spec(f"{cell}-{feasibility}-{i}", cell, feasibility)
                specs.append(s)
                for repeat in s.repeat_ids:
                    runs.append(run(s, repeat, passes=i % 2 == 0,
                                    infra=not (i < unknown and repeat == "3")))
    return specs, runs


def test_frozen_support_class_weight_and_category_decomposition():
    specs, runs = dataset()
    report = organization_report(specs, runs, registered_cells=("c",), resamples=100)
    assert report["status"] == "valid"
    assert report["point"] == pytest.approx(60)  # .5*80 + 1*20
    assert sum(report["category_points"].values()) == pytest.approx(30*report["components"]["Qorg"])
    assert report["components"]["V"] <= report["components"]["Qorg"]
    assert report["physical_qualified"] is report["publication_ready"] is False


def test_d1_retains_unknown_funnel_when_coverage_passes():
    specs, runs = dataset(n=40, unknown=8)
    report = organization_report(specs, runs, registered_cells=("c",), resamples=100)
    assert report["status"] == "valid"
    assert len(report["exclusion_funnel"]) == 16
    assert report["coverage"]["deployable"]["c"]["retained_fraction"] == .8
    specs, runs = dataset(n=40, unknown=9)
    report = organization_report(specs, runs, registered_cells=("c",), resamples=100)
    assert report["status"] == "na"
    assert report["point"] is None
    assert report["components"]["Qorg"] is not None


def test_no_cell_reweighting_or_low_floor_override():
    specs, runs = dataset()
    result = organization_report(specs, runs, registered_cells=("c", "missing"), resamples=100)
    assert result["status"] == "na" and result["components"]["V"] is None
    with pytest.raises(ValueError, match="30 instances"):
        organization_report(specs, runs, registered_cells=("c",), minimum_instances=1)
    with pytest.raises(ValueError, match="roster"):
        organization_report(specs, runs[:-1], registered_cells=("c",))


def test_metric_missingness_does_not_change_infrastructure_count():
    specs, runs = dataset(n=40)
    runs = [replace(r, checks=()) if r.instance_id.endswith("deployable-0") else r for r in runs]
    report = organization_report(specs, runs, registered_cells=("c",), resamples=100)
    counts = report["coverage"]["deployable"]["c"]
    assert counts["infrastructure_valid"] == 40
    assert counts["retained_instances"] == 39


def test_unverified_feasibility_excluded_not_infeasible():
    specs, runs = dataset()
    unverified = spec("unverified", feasibility="feasibility_unverified")
    specs.append(unverified)
    runs.extend(run(unverified, r) for r in unverified.repeat_ids)
    report = organization_report(specs, runs, registered_cells=("c",), resamples=100)
    assert report["coverage"]["team_state_infeasible"]["c"]["planned"] == 30
    assert report["exclusion_funnel"][0]["reasons"] == ["feasibility_unverified"]


def test_json_round_trip_and_frozen_provenance():
    specs, runs = dataset()
    payload = {"instances": [asdict(s) for s in specs], "runs": [asdict(r) for r in runs],
               "registered_cells": ["c"], "inference": {"resamples": 100}}
    result = organization_report_from_payload(json.loads(json.dumps(payload)))
    assert result == organization_report(specs, runs, registered_cells=["c"], resamples=100)


def test_constant_bootstrap_flagged_not_zero_width_certainty():
    specs, runs = dataset()
    runs = [replace(r, checks=tuple(replace(c, passed=True) for c in r.checks)) for r in runs]
    report = organization_report(specs, runs, registered_cells=("c",), resamples=100)
    assert report["point"] == pytest.approx(100)
    assert report["status"] == "unstable" and report["interval"] is None


def test_formal_support_gate_rejects_scalar_and_single_cell_diagnostics():
    assert organization_support_errors({"status": "valid"}) == ["d1_first_plan_evaluator_report_required"]
    specs, runs = dataset()
    report = organization_report(specs, runs, registered_cells=("c",), resamples=100)
    assert organization_support_errors(report) == ["d1_v1_requires_48_frozen_cells"]
    # Copying a reportable boolean cannot disguise an actual low support count.
    report["coverage"]["deployable"]["c"]["retained_instances"] = 1
    assert "d1_class_cell_coverage_insufficient" in organization_support_errors(report)


def test_analysis_cli_recomputes_raw_d1_journal(tmp_path):
    import subprocess
    import sys
    from benchmark.scorebook_v3 import PAPER_COMMIT
    specs, runs = dataset()
    payload = {"paper_commit": PAPER_COMMIT, "score_id": "D1", "scope": "synthetic_engineering_fixture",
               "instances": [asdict(s) for s in specs], "runs": [asdict(r) for r in runs],
               "registered_cells": ["c"], "inference": {"resamples": 100}}
    source, output = tmp_path / "input.json", tmp_path / "output.json"
    source.write_text(json.dumps(payload))
    subprocess.run([sys.executable, "-m", "benchmark.analysis_v3", str(source), "--output", str(output)], check=True)
    expected = organization_report_from_payload(payload)
    expected["scope"] = payload["scope"]
    assert json.loads(output.read_text()) == json.loads(json.dumps(expected))
