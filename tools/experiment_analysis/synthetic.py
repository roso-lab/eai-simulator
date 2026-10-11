"""Reproducible mathematical controls. No scene, task or measured result."""

from __future__ import annotations

from . import ANALYSIS_VERSION
from .schema import digest, integer


def generate(*, seed: int = 773, instances: int = 40, resamples: int = 400) -> tuple[dict, dict, dict]:
    integer(instances, 1)
    if instances % 20:
        raise ValueError("analytic controls require instances divisible by 20")
    systems = ["toy_reference", "toy_candidate"]
    repeats = ["r1", "r2", "r3"]
    units, rows, cells = [], [], []
    artifact = digest({"source": "synthetic_analytic_controls", "seed": seed})

    def cell(cid: str, domain: str) -> None:
        cells.append(dict(id=cid, domain=domain, primary_label="synthetic_relation", difficulty="control",
                          min_instances=30, min_coverage=.8))

    def unit(uid: str, cid: str, members: dict, supports: list, family: str | None = None) -> dict:
        item = dict(id=uid, cell_id=cid, members=members, repeats=repeats, supports=supports, infrastructure_status="valid")
        if family is not None:
            item["family_id"] = family
        units.append(item)
        return item

    def record(item: dict, member: str, system: str, condition: str, repeat: str, values: dict, outcome: str) -> None:
        rows.append(dict(unit_id=item["id"], base_instance_id=item["members"][member], member=member,
                         system_id=system, condition_id=condition, branch_role="fact", checkpoint_round="e0",
                         repeat_id=repeat, status="valid", method_outcome=outcome, values=values,
                         artifact_digest=artifact, pair_context_digest=digest([item["id"], system, repeat, "frozen_control"])))

    for cid in ("control_a", "control_b"):
        cell(cid, "synthetic")
        for i in range(instances):
            item = unit(f"{cid}/{i}", cid, {"main": f"base/{cid}/{i}"}, ["main"])
            for system in systems:
                for repeat in repeats:
                    for condition, interaction in (("nominal", 1.0), ("stress", .7), ("g", .8), ("fg", .4)):
                        success = int(i % 20 < (15 if system == "toy_reference" else 16))
                        if condition == "stress":
                            success = int(i % 20 < (10 if system == "toy_reference" else 12))
                        values = {"success": success, "plan": int(i % 5 != 0),
                                  "opportunity": int(i % 2 == 0), "satisfaction": int(i % 4 == 0),
                                  "interaction": interaction, "cost_bits": 1000 + i * 10,
                                  "progress": .5 + success / 2}
                        record(item, "main", system, condition, repeat, values, "completed" if success else "method_failure")
                    cause = ("resume", "fail", "safe", "infeasible", "censor")[i % 5]
                    value = {"time": 10 if cause == "censor" else 2 + i % 5, "cause": cause}
                    record(item, "main", system, "event", repeat, {"recovery": value},
                           {"resume": "completed", "fail": "method_failure", "safe": "safe_stop",
                            "infeasible": "infeasible", "censor": "censored"}[cause])
    families = []
    cell("transfer", "synthetic_pair")
    for j in range(3):
        fid = f"family{j}"
        families.append(dict(id=fid, anchor=0, scale=1, orientation=1, min_pairs=30, min_coverage=.8,
                             certificate_digest=digest(["synthetic_certificate", fid])))
        for i in range(instances + 4 * j):
            uid = f"{fid}/{i}"
            item = unit(uid, "transfer", {"src": f"base/{uid}/src", "tgt": f"base/{uid}/tgt"}, ["transfer"], fid)
            for system in systems:
                for repeat in repeats:
                    for member, threshold in (("src", 3), ("tgt", (2, 1, 3)[j])):
                        success = int(i % 4 < threshold)
                        record(item, member, system, "nominal", repeat, {"success": success}, "completed" if success else "method_failure")

    def term(measure: str, condition: str = "nominal", member: str = "main") -> dict:
        return dict(member=member, condition_id=condition, branch_role="fact", checkpoint_round="e0", measure=measure)

    def est(eid: str, kind: str, terms: list, **extra) -> dict:
        return dict(id=eid, kind=kind, support_id="main", cells=["control_a", "control_b"], terms=terms,
                    min_repeats=3, min_units=30, min_coverage=.8, **extra)

    estimands = [
        est("control_plan", "mean", [term("plan")]),
        est("control_success", "mean", [term("success")]),
        est("control_retention", "ratio_of_means", [term("success", "stress"), term("success")], denominator_floor=.5),
        est("control_temporal_conditional", "ratio_of_means", [term("satisfaction"), term("opportunity")], denominator_floor=0),
        est("control_recovery", "cif", [term("recovery", "event")], horizon=10, cause="resume"),
        est("control_rmst", "rmst", [term("recovery", "event")], horizon=10),
        est("control_cost", "mean", [term("cost_bits")]),
        est("control_interaction", "linear", [term("interaction", c) for c in ("nominal", "stress", "g", "fg")],
            coefficients=[1, -1, -1, 1]),
    ]
    transfer = est("control_transfer", "family_gap", [term("success", member="src"), term("success", member="tgt")], families=families)
    transfer.update(support_id="transfer", cells=["transfer"])
    estimands.append(transfer)
    comparison = est("control_system_difference", "linear", [term("success"), term("success")], coefficients=[1, -1])
    comparison["terms"][0]["system_id"] = "toy_candidate"
    comparison["terms"][1]["system_id"] = "toy_reference"
    estimands.append(comparison)
    design = dict(schema=ANALYSIS_VERSION + ".design", id="synthetic_math_controls_v1", authority="synthetic_test_generator",
                  protocol_digest=digest({"reference": "Overleaf v773", "use": "math_conformance_only"}),
                  systems=systems, cells=cells, units=units,
                  measures=[dict(id=name, kind="scalar", unit="fraction", bounds=[0, 1]) for name in
                            ("success", "plan", "opportunity", "satisfaction", "progress", "interaction")]
                  + [dict(id="cost_bits", kind="scalar", unit="bit", bounds=[0, None]),
                     dict(id="recovery", kind="event", unit="tick", causes=["resume", "fail", "safe", "infeasible"])],
                  estimands=estimands, bootstrap=dict(seed=seed, resamples=resamples))
    data = dict(schema=ANALYSIS_VERSION + ".dataset", design_digest=digest(design),
                provenance=dict(kind="synthetic", producer="eai.synthetic_math_controls.v1", source_revision="generator.v1",
                                evidence_digests=[artifact]), records=rows)
    expected = dict(control_plan=.8, control_temporal_conditional=.5, control_recovery=.2,
                    control_rmst=4.8, control_cost=1000 + (instances - 1) * 5,
                    control_interaction=-.1, control_transfer=-.25,
                    control_system_difference=.05,
                    control_success={"toy_reference": .75, "toy_candidate": .8},
                    control_retention={"toy_reference": 2 / 3, "toy_candidate": .75})
    return design, data, expected
