"""Reproducible engineering acceptance, explicitly not paper experiments.

Writes observed local-catalog readiness and deterministic synthetic analysis
fixtures. It never certifies a case, invents calibration, or ranks a system.
Run through tools.project_runtime to fix project-local imports and outputs.
"""
from dataclasses import asdict
import argparse
import hashlib
import json
from pathlib import Path
from collections import Counter

from .analysis_v3 import ScheduledRunV3, analyze_dimension
from .defects_v3 import DefectModulesV3, StateMessageV3
from .registration_v3 import ExperimentRegistrationV3, formal_readiness
from .scorebook_v3 import PAPER_COMMIT
from .task_catalog import load_default_catalog, catalog_report
from .statistics_v3 import hoeffding_sample_size


def _portable(value):
    if isinstance(value, float) and value == float("inf"): return "infinity"
    if isinstance(value, dict): return {k: _portable(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)): return [_portable(v) for v in value]
    return value


def write_json(path, payload):
    path.write_text(json.dumps(_portable(payload), ensure_ascii=False, indent=2, allow_nan=False)+"\n")


def _organization_fixture():
    from .organization_v3 import (OrganizationCheckV3, OrganizationInstanceV3,
        OrganizationRunV3, OrganizationEventV3, OrganizationCheckObservationV3)
    instances, runs = [], []
    for feasibility in ("deployable", "team_state_infeasible"):
        for i in range(30):
            spec = OrganizationInstanceV3(f"synthetic/{feasibility}/{i}", "fixture", feasibility,
                "synthetic-certificate", "synthetic-contract", "synthetic-physical-model", 10, 20,
                (OrganizationCheckV3("cap", "capability", ("registered",)),))
            instances.append(asdict(spec))
            for repeat in spec.repeat_ids:
                if feasibility == "deployable":
                    events = (OrganizationEventV3("first", 5, 0, "commit", "synthetic-event", "synthetic-plan", True),)
                    checks = (OrganizationCheckObservationV3("cap", "registered", i % 2 == 0, "synthetic-check"),)
                else:
                    events = (OrganizationEventV3("decl", 5, 0, "declaration", "synthetic-event", declaration="not_deployable"),
                              OrganizationEventV3("stop", 6, 0, "safe_stop", "synthetic-event", safe_stop_verified=True))
                    checks = ()
                runs.append(asdict(OrganizationRunV3(spec.instance_id, repeat, "synthetic-system", spec.digest(),
                    "synthetic-run", True, 20, events, checks, "synthetic-plan", spec.physical_model_digest)))
    return {"score_id": "D1", "paper_commit": PAPER_COMMIT, "scope": "synthetic_engineering_fixture",
            "instances": instances, "runs": runs, "registered_cells": ["fixture"],
            "inference": {"seed": 20261020, "resamples": 1000}}


def _dynamic_timeline_fixture():
    from .dynamic_value_v3 import (DynamicValueRegistrationV3, DynamicValueTimelineV3,
        OutcomeValueV3, OutcomeStateV3, ServiceStateV3, DensitySegmentV3, evaluate_dynamic_value_v3)
    rules = (
        DynamicValueRegistrationV3("deadline", "deadline", 0, 10,
            (OutcomeValueV3("accepted", 1.),), deadline_tick=10),
        DynamicValueRegistrationV3("time", "time_valued", 0, 10,
            (OutcomeValueV3("accepted", 1.),), deadline_tick=10,
            density=(DensitySegmentV3(0, 10, .1),)),
        DynamicValueRegistrationV3("service", "continuous_supply", 0, 10,
            (OutcomeValueV3("coverage", 1.),), demand_start_tick=0,
            demand_end_tick=10, stability_ticks=3),
    )
    branches = {}
    for branch, completion in (("healthy", 2), ("loss", 7)):
        timelines, evaluations = [], []
        for rule in rules:
            states = () if rule.item_type == "continuous_supply" else (
                OutcomeStateV3("accepted", 0, False, "synthetic-initial"),
                OutcomeStateV3("accepted", completion, True, "synthetic-acceptance"))
            service = (ServiceStateV3(0, False, "synthetic-off"),
                       ServiceStateV3(completion, True, "synthetic-on")) if rule.item_type == "continuous_supply" else ()
            timeline = DynamicValueTimelineV3(rule.item_id, 10, states, service,
                operation_evidence_hash="synthetic-operation", remaining_feasibility_hash="synthetic-feasibility",
                progress_evidence_hash="synthetic-progress")
            timelines.append(asdict(timeline))
            evaluations.append(asdict(evaluate_dynamic_value_v3(rule, timeline)))
        scores = [r["legal_completion_fraction"] if rule.item_type == "deadline" else
                  (r["legal_completion_fraction"]+r["service_fraction"])/2
                  for rule, r in zip(rules, evaluations)]
        branches[branch] = {"timelines": timelines, "evaluations": evaluations,
                            "run_score": 100*sum(scores)/len(scores)}
    return {"scope": "synthetic_engineering_fixture", "physical_qualified": False,
            "rules": [asdict(r) for r in rules], "branches": branches,
            "loss_minus_healthy_points": branches["loss"]["run_score"]-branches["healthy"]["run_score"],
            "meaning": "shared synthetic value rules only; no physical common-checkpoint qualification"}


def verify(output: Path):
    root = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if not output.is_relative_to(root):
        raise ValueError("experiment artifacts must remain inside HeteroCoord")
    output.mkdir(parents=True, exist_ok=True)
    catalog = load_default_catalog()
    audit = catalog_report(catalog)
    audit["certification_statuses"] = dict(Counter(a.certification_status for a in catalog))
    audit["oracle_statuses"] = dict(Counter(a.oracle_status for a in catalog))
    audit["meaning"] = "authored anchors are not independently certified base instances"
    write_json(output / "catalog_audit.json", audit)
    from algorithm.system.coordination.registry import system_algorithm_records
    baselines = system_algorithm_records()
    write_json(output / "baseline_inventory.json", {"entries": baselines, "registered": sum(r["availability"] == "registered" for r in baselines), "unavailable": sum(r["availability"] == "N/A" for r in baselines), "qualification": "implementation registration does not supply current physical benchmark certification"})
    registration = ExperimentRegistrationV3(PAPER_COMMIT, audit["catalog_digest"], *([None]*10))
    # Known local implementation inventory is frozen as evidence, but never
    # promoted to a qualified baseline manifest.
    write_json(output / "source_inventory.json", {"files": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root / "benchmark").glob("*_v3.py"))}, "qualification": "source identity only"})
    write_json(output / "registration.json", asdict(registration))
    readiness = formal_readiness(registration, case_report={"status": "na"}, baseline_report={"status": "na"}, calibration_report=None)
    readiness["scope"] = "formal paper experiment; no certification inferred from anchor count"
    write_json(output / "formal_readiness.json", readiness)
    write_json(output / "D4_timeline_example.json", _dynamic_timeline_fixture())
    results = []
    for dimension in range(1, 7):
        if dimension == 1:
            from .organization_v3 import organization_report_from_payload
            payload = _organization_fixture()
            write_json(output / "D1_input.json", payload)
            result = organization_report_from_payload(payload)
            result["scope"] = payload["scope"]
            write_json(output / "D1_report.json", result)
            results.append({"score_id": "D1", "status": result["status"],
                            "identification": result.get("identification"), "interval": result.get("interval")})
            continue
        if dimension == 3:
            from .relations_v3 import RelationRunV3, d3_relation_report
            rows = [RelationRunV3(str(i), "production", "L1", "nominal", str(s), {key:float((i+s)%3 != 0) for key in ("P2", "P3J", "P3S", "P4")}, "synthetic-evidence") for i in range(30) for s in range(3)]
            # An explicit fixture partition exercises computation only.  The
            # current-paper partition conflict remains a formal blocker.
            inference = {"seed": 20261020, "resamples": 1000, "partition_mode": "domain_difficulty_condition",
                         "registered_cells": {key: [("production", "L1", "nominal")] for key in ("P2", "P3J", "P3S", "P4")}}
            write_json(output / "D3_input.json", {"scope": "synthetic_engineering_fixture", "paper_commit": PAPER_COMMIT, "score_id": "D3", "runs": [asdict(r) for r in rows], "inference": inference})
            result = d3_relation_report(rows, **inference)
            result["scope"] = "synthetic_engineering_fixture"
            write_json(output / "D3_report.json", result)
            results.append({"score_id": "D3", "status": result["status"], "identification": result.get("identification"), "interval": result.get("interval")})
            continue
        rows = []
        for i in range(30):
            for repeat in range(3):
                # Deterministic synthetic outcomes, deliberately labelled.
                values = (1., float(i%2 == 0), float(i%3 == 0), float(i%5 == 0)) if dimension == 2 else (float((i+repeat)%3 != 0),)
                rows.append(ScheduledRunV3(f"fixture/{i}/{repeat}", str(i), "fixture", str(repeat), "synthetic-checkpoint", "synthetic-evidence", values, weight=1/90 if dimension >= 5 else None))
        score = f"D{dimension}"
        payload = {"scope": "synthetic_engineering_fixture", "paper_commit": PAPER_COMMIT, "score_id": score, "runs": [asdict(r) for r in rows], "stratum_weights": {"fixture": 1}, "inference": {"seed": 20261020, "resamples": 1000}}
        catalog = None
        if dimension >= 5:
            from .fixed_catalog_v3 import FixedCatalogV3, FixedRunSpecV3
            catalog = FixedCatalogV3(score, tuple(FixedRunSpecV3(r.run_id, r.instance_id, r.stratum, r.repeat_id, r.checkpoint_digest, r.weight, r.run_id, "family", r.instance_id, "unit") for r in rows))
            payload["fixed_catalog"] = asdict(catalog)
        write_json(output / f"{score}_input.json", payload)
        result = analyze_dimension(score, rows, stratum_weights={"fixture": 1}, seed=20261020, resamples=1000, fixed_catalog=catalog)
        result["scope"] = "synthetic_engineering_fixture"
        write_json(output / f"{score}_report.json", result)
        results.append({"score_id": score, "status": result["status"], "identification": result.get("identification"), "interval": result.get("interval")})
    msg = StateMessageV3("state", 0, 2, {"robot": "r1"})
    variants = {}
    for name in ("original", "stale_message", "no_reallocation", "sham"):
        module = DefectModulesV3(name)
        variants[name] = {"nominal_accepted": len(module.consume([msg], tick=1)), "delayed_accepted": len(module.consume([msg], tick=4)), "nominal_assignment": module.reassign(function_loss=False, current_assignment={"work": "r1"}, propose=lambda: {"work": "r2"}), "loss_assignment": module.reassign(function_loss=True, current_assignment={"work": "r1"}, propose=lambda: {"work": "r2"})}
    write_json(output / "defect_module_checks.json", {"scope": "synthetic_module_behavior_only", "variants": variants, "physical_attribution_qualified": False, "missing": ["registered physical benchmark integration", "nominal equivalence sample", "six-score predicted pattern", "held-out module restoration"]})
    write_json(output / "summary.json", {"paper_commit": PAPER_COMMIT, "scope": "engineering_acceptance_only", "scores": results, "formal_ready": readiness["ready"], "blockers": readiness["blockers"], "fixed_catalog_precision_plan": hoeffding_sample_size()})
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob("*.json")) if p.name != "manifest.json"}
    write_json(output / "manifest.json", manifest)
    return {"artifact_directory": str(output), "formal_ready": readiness["ready"], "blockers": readiness["blockers"], "synthetic_reports": len(results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/p0-p5/acceptance"))
    args = parser.parse_args()
    print(json.dumps(verify(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
