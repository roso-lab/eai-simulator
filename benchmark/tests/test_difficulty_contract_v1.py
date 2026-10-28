from __future__ import annotations

import copy
import hashlib
import json

import pytest

from benchmark import difficulty_contract_v1 as difficulty
from benchmark.difficulty_contract_v1 import (
    BASE_CONTRACT_SCHEMA,
    COMMON_PAIR_REGISTRY_SCHEMA,
    COMPONENTS,
    COMPONENT_SPECS,
    EVIDENCE_SCHEMA,
    FRESH_SCHEMA,
    PILOT_SCHEMA,
    VALIDATION_REGISTRY_SCHEMA,
    ComponentSpec,
    DifficultyContractError,
    bind_base_contract,
    certify_fresh_ordering,
    certify_pilot_sweeps,
    common_pair_registry_digest,
    difficulty_certification,
    evaluate_difficulty_contract,
    score_component,
    validate_assessment,
    validate_certification,
    validate_common_pair_tiers,
    validation_registry_digest,
)
from benchmark.scorebook_v3 import PAPER_COMMIT


def _base_contract() -> dict:
    return bind_base_contract({
        "schema": BASE_CONTRACT_SCHEMA,
        "domain": "production",
        "primitive": "P1",
        "robots": ["r1", "r2"],
        "team_min": 2,
        "obligations": [
            {
                "obligation_id": "o1",
                "capable_robots": ["r1", "r2"],
                "min_coalition": 1,
                "resource_demands": {"shared": 1},
                "information_needs": ["item"],
            },
            {
                "obligation_id": "o2",
                "capable_robots": ["r1", "r2"],
                "min_coalition": 1,
                "resource_demands": {"shared": 1},
                "information_needs": ["item"],
            },
        ],
        "precedence_edges": [{"before": "o1", "after": "o2"}],
        "resource_capacities": {"shared": 1},
        "navigation": {
            "nodes": ["start", "site1", "site2"],
            "edges": [
                {"from": "start", "to": "site1", "bidirectional": True},
                {"from": "site1", "to": "site2", "bidirectional": True},
            ],
            "robot_start_nodes": {"r1": "start", "r2": "start"},
            "obligation_site_nodes": {"o1": "site1", "o2": "site2"},
            "safety_constrained_nodes": [],
        },
        "observation_rights": {"item": ["r1", "r2"]},
        "milestones": [{"milestone_id": "done", "alternatives": ["fixed"]}],
        "admissible_decomposition_count": 1,
        "hidden_variables": [],
    })


def _digest(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _contract_for(component: str, bin_value: int) -> dict:
    contract = _base_contract()

    def resize(count: int, *, robot_count: int | None = None) -> None:
        count_robots = max(2, count) if robot_count is None else robot_count
        robots = [f"r{index}" for index in range(1, count_robots + 1)]
        obligations = []
        for index in range(1, count + 1):
            obligations.append({
                "obligation_id": f"o{index}",
                "capable_robots": list(robots),
                "min_coalition": 1,
                "resource_demands": {"shared": 1 if index <= 2 else 0},
                "information_needs": ["item"],
            })
        contract["robots"] = robots
        contract["obligations"] = obligations
        contract["precedence_edges"] = [{"before": "o1", "after": "o2"}] if count >= 2 else []
        sites = [f"site{index}" for index in range(1, count + 1)]
        contract["navigation"] = {
            "nodes": ["start", *sites],
            "edges": [{"from": "start", "to": site, "bidirectional": True} for site in sites],
            "robot_start_nodes": {robot: "start" for robot in robots},
            "obligation_site_nodes": {f"o{index}": f"site{index}" for index in range(1, count + 1)},
            "safety_constrained_nodes": [],
        }
        contract["observation_rights"] = {"item": list(robots)}

    if component == "dependency_depth":
        resize(2 + bin_value)
        contract["precedence_edges"] = [
            {"before": f"o{index}", "after": f"o{index + 1}"}
            for index in range(1, 2 + bin_value)
        ]
    elif component == "coordination_width":
        resize(2, robot_count=2 + bin_value)
        contract["team_min"] = 2 + bin_value
    elif component == "capability_scarcity":
        resize(2 + bin_value, robot_count=2)
    elif component == "contention":
        contract["obligations"][1]["resource_demands"]["shared"] = 1 + bin_value
    elif component == "hazard":
        resize(max(2, bin_value))
        contract["navigation"]["safety_constrained_nodes"] = [f"site{index}" for index in range(1, bin_value + 1)]
    elif component == "communication_demand":
        resize(max(2, bin_value))
        for index, obligation in enumerate(contract["obligations"], start=1):
            if index <= bin_value:
                obligation["information_needs"] = [f"hidden-{index}"]
                contract["observation_rights"][f"hidden-{index}"] = []
    elif component == "branching_load":
        counts = (1, 3, 8, 23)
        count = counts[bin_value]
        contract["milestones"][0]["alternatives"] = [f"a{index}" for index in range(count)]
        contract["admissible_decomposition_count"] = count
    elif component == "uncertainty":
        if bin_value:
            sizes = (None, 4, 16, 64)
            contract["hidden_variables"] = [{
                "variable_id": "hidden",
                "candidate_set": [f"s{index}" for index in range(sizes[bin_value])],
            }]
    else:
        raise AssertionError(component)
    return bind_base_contract(contract)


def test_eight_raw_components_are_contract_only_and_hash_bound() -> None:
    result = evaluate_difficulty_contract(_base_contract())
    assert list(result["components"]) == list(COMPONENTS)
    assert {name: row["raw"] for name, row in result["components"].items()} == {
        "dependency_depth": 2.0,
        "coordination_width": 2.0,
        "capability_scarcity": 1.0,
        "contention": 2.0,
        "hazard": 0.0,
        "communication_demand": 0.0,
        "branching_load": 0.0,
        "uncertainty": 0.0,
    }
    assert result["tier"] == 1
    assert result["outcome_independent"] is True
    assert result["formal_gates"]["paper_score_eligible"] is False


@pytest.mark.parametrize("fraction, expected_bin", [(0, 0), (0.25, 1), (0.5, 2), (0.75, 3), (1, 3)])
def test_q_and_equal_width_bin_boundaries(fraction: float, expected_bin: int) -> None:
    spec = COMPONENT_SPECS["dependency_depth"]
    result = score_component("dependency_depth", spec.lower + fraction * (spec.upper - spec.lower))
    assert result["q"] == fraction
    assert result["bin"] == expected_bin


def test_missing_cycle_hash_tamper_and_degenerate_bounds_fail_closed() -> None:
    missing = _base_contract()
    missing.pop("navigation")
    with pytest.raises(DifficultyContractError, match="schema fields"):
        evaluate_difficulty_contract(missing)

    tampered = _base_contract()
    tampered["team_min"] = 3
    with pytest.raises(DifficultyContractError, match="base_contract_hash"):
        evaluate_difficulty_contract(tampered)

    cyclic = _base_contract()
    cyclic["precedence_edges"].append({"before": "o2", "after": "o1"})
    cyclic = bind_base_contract(cyclic)
    with pytest.raises(DifficultyContractError, match="cycle"):
        evaluate_difficulty_contract(cyclic)

    with pytest.raises(ValueError, match="non-degenerate"):
        ComponentSpec(1, 1)


def test_no_precedence_and_no_resource_are_valid_clipped_bin_zero() -> None:
    contract = _base_contract()
    contract["precedence_edges"] = []
    contract["resource_capacities"] = {}
    for obligation in contract["obligations"]:
        obligation["resource_demands"] = {}
    result = evaluate_difficulty_contract(bind_base_contract(contract))
    assert result["components"]["dependency_depth"]["raw"] == 1
    assert result["components"]["dependency_depth"]["bin"] == 0
    assert result["components"]["contention"]["raw"] == 0
    assert result["components"]["contention"]["bin"] == 0


def test_hazard_is_recomputed_by_graph_search_and_branching_uses_admissible_count() -> None:
    contract = _base_contract()
    contract["navigation"]["safety_constrained_nodes"] = ["site1"]
    contract["milestones"][0]["alternatives"] = ["a", "b", "c", "d"]
    contract["admissible_decomposition_count"] = 2
    result = evaluate_difficulty_contract(bind_base_contract(contract))
    # site1 is itself constrained and is also the only route to site2.
    assert result["components"]["hazard"]["raw"] == 2
    assert result["components"]["branching_load"]["raw"] == 1

    unknown = _base_contract()
    unknown["navigation"]["obligation_site_nodes"]["o1"] = "missing"
    with pytest.raises(DifficultyContractError, match="unknown node"):
        evaluate_difficulty_contract(bind_base_contract(unknown))


def _common_pair_material() -> tuple[dict, dict, dict, str]:
    left = _base_contract()
    right = copy.deepcopy(left)
    right["domain"] = "service"
    right["primitive"] = "P4"
    right = bind_base_contract(right)
    registry = {
        "schema": COMMON_PAIR_REGISTRY_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "pairs": [{
            "common_anchor_id": "common-p1-p4",
            "left_side_id": "prod-base-01",
            "right_side_id": "svc-base-01",
            "left_domain": "production",
            "left_primitive": "P1",
            "left_base_contract_hash": left["base_contract_hash"],
            "right_domain": "service",
            "right_primitive": "P4",
            "right_base_contract_hash": right["base_contract_hash"],
        }],
    }
    return left, right, registry, common_pair_registry_digest(registry)


def _validate_pair(left: dict, right: dict, registry: dict, digest: str, **overrides) -> dict:
    arguments = {
        "common_anchor_id": "common-p1-p4",
        "left_side_id": "prod-base-01",
        "right_side_id": "svc-base-01",
        "pair_registry": registry,
        "expected_pair_registry_digest": digest,
    }
    arguments.update(overrides)
    return validate_common_pair_tiers(left, right, **arguments)


def test_common_pair_is_externally_registered_content_bound_and_fail_closed() -> None:
    left, right, registry, digest = _common_pair_material()
    receipt = _validate_pair(left, right, registry, digest)
    assert receipt["matched"] is True
    assert receipt["common_anchor_id"] == "common-p1-p4"
    assert receipt["pair_registry_digest"] == digest
    assert receipt["left_assessment_digest"] != receipt["right_assessment_digest"]
    assert receipt["formal_gates"] == difficulty.FORMAL_GATES

    arbitrary = copy.deepcopy(right)
    arbitrary["primitive"] = "P3"
    arbitrary = bind_base_contract(arbitrary)
    with pytest.raises(DifficultyContractError, match="base-contract hash"):
        _validate_pair(left, arbitrary, registry, digest)
    with pytest.raises(DifficultyContractError, match="base-contract hash"):
        _validate_pair(left, left, registry, digest)
    with pytest.raises(DifficultyContractError, match="side identity"):
        _validate_pair(
            left, right, registry, digest,
            left_side_id="svc-base-01", right_side_id="prod-base-01",
        )

    altered_hash = copy.deepcopy(registry)
    altered_hash["pairs"][0]["left_base_contract_hash"] = "0" * 64
    with pytest.raises(DifficultyContractError, match="base-contract hash"):
        _validate_pair(left, right, altered_hash, common_pair_registry_digest(altered_hash))

    replacement = copy.deepcopy(registry)
    replacement["pairs"][0]["common_anchor_id"] = "replacement"
    assert common_pair_registry_digest(replacement) != digest
    with pytest.raises(DifficultyContractError, match="externally pinned"):
        _validate_pair(left, right, replacement, digest)


def test_common_pair_rejects_registered_tier_mismatch_and_assessment_tamper() -> None:
    left, right, registry, _ = _common_pair_material()
    harder = copy.deepcopy(right)
    harder["robots"].append("r3")
    harder["team_min"] = 3
    for obligation in harder["obligations"]:
        obligation["capable_robots"].append("r3")
    harder["navigation"]["robot_start_nodes"]["r3"] = "start"
    harder["observation_rights"]["item"].append("r3")
    harder = bind_base_contract(harder)
    registry["pairs"][0]["right_base_contract_hash"] = harder["base_contract_hash"]
    digest = common_pair_registry_digest(registry)
    assert evaluate_difficulty_contract(harder)["tier"] == 2
    with pytest.raises(DifficultyContractError, match="tier mismatch"):
        _validate_pair(left, harder, registry, digest)

    tier1 = evaluate_difficulty_contract(left)
    changed = copy.deepcopy(tier1)
    changed["components"]["coordination_width"] = score_component("coordination_width", 3)
    changed["tier"] = 2
    changed["assessment_digest"] = _digest({key: value for key, value in changed.items() if key != "assessment_digest"})
    with pytest.raises(DifficultyContractError, match="does not reproduce"):
        validate_assessment(changed, left)


def test_cochran_armitage_uses_conditional_hypergeometric_variance() -> None:
    successes = (8, 6, 4, 2)
    trials = (10, 10, 10, 10)
    report = difficulty._trend(successes, trials, 0.05)
    total = sum(trials)
    proportion = sum(successes) / total
    mean_score = sum(index * trials[index] for index in range(4)) / total
    variance = proportion * (1 - proportion) * total / (total - 1) * sum(
        trials[index] * (index - mean_score) ** 2 for index in range(4)
    )
    numerator = sum(index * (successes[index] - trials[index] * proportion) for index in range(4))
    assert report["z"] == pytest.approx(numerator / variance**0.5)
    assert report["p_decrease"] < 0.05
    assert report["direction"] == "significant_decrease"


def _validation_bundle() -> tuple[dict, str, dict, dict, dict[str, dict]]:
    contracts: dict[str, dict] = {}
    pilot_roster = []
    pilot_observations = []
    implementations = ("pilot-reference", "pilot-alternate")
    for implementation_id in implementations:
        for component in COMPONENTS:
            for bin_value, successes in enumerate((95, 70, 30, 5)):
                contract = _contract_for(component, bin_value)
                contract_hash = contract["base_contract_hash"]
                contracts[contract_hash] = contract
                observation_id = f"pilot:{implementation_id}:{component}:{bin_value}:instance-0"
                pilot_roster.append({
                    "observation_id": observation_id,
                    "implementation_id": implementation_id,
                    "component": component,
                    "bin": bin_value,
                    "instance_id": "instance-0",
                    "base_contract_hash": contract_hash,
                })
                pilot_observations.append({
                    "observation_id": observation_id,
                    "successes": successes,
                    "trials": 100,
                })

    fresh_instances = ("fresh-0", "fresh-1")
    fresh_roster = []
    fresh_observations = []
    for implementation_id in implementations:
        for instance_id in fresh_instances:
            for tier, successes in enumerate((90, 70, 50, 30), start=1):
                contract = _contract_for("dependency_depth", tier - 1)
                contract_hash = contract["base_contract_hash"]
                contracts[contract_hash] = contract
                observation_id = f"fresh:production:P1:{implementation_id}:{tier}:{instance_id}"
                fresh_roster.append({
                    "observation_id": observation_id,
                    "domain": "production",
                    "primitive": "P1",
                    "implementation_id": implementation_id,
                    "tier": tier,
                    "instance_id": instance_id,
                    "base_contract_hash": contract_hash,
                })
                fresh_observations.append({
                    "observation_id": observation_id,
                    "successes": successes,
                    "trials": 100,
                })

    registry = {
        "schema": VALIDATION_REGISTRY_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "generator_revision": "generator-source-dac7b94-r1",
        "pilot_alpha": 0.05,
        "fresh_tolerance": 0.05,
        "implementations": [
            {"implementation_id": implementation_id, "implementation_role": "registered_pilot", "candidate": False}
            for implementation_id in implementations
        ] + [{"implementation_id": "candidate-system", "implementation_role": "candidate", "candidate": True}],
        "pilot_roster": pilot_roster,
        "fresh_cells": [{"domain": "production", "primitive": "P1"}],
        "fresh_instances": [
            {"domain": "production", "primitive": "P1", "instance_id": instance_id}
            for instance_id in fresh_instances
        ],
        "fresh_roster": fresh_roster,
        "allowed_base_contract_hashes": sorted(contracts),
    }
    pilot_evidence = {"schema": EVIDENCE_SCHEMA, "observations": pilot_observations}
    fresh_evidence = {"schema": EVIDENCE_SCHEMA, "observations": fresh_observations}
    return registry, validation_registry_digest(registry), pilot_evidence, fresh_evidence, contracts


def _certify_bundle(registry, digest, pilot_evidence, fresh_evidence, contracts):
    pilot = certify_pilot_sweeps(pilot_evidence, contracts, registry, digest)
    fresh = certify_fresh_ordering(fresh_evidence, contracts, registry, digest)
    receipt = difficulty_certification(
        pilot, fresh, pilot_evidence, fresh_evidence, contracts, registry, digest,
    )
    return pilot, fresh, receipt


def test_registry_bound_reports_cover_every_registered_observation() -> None:
    registry, digest, pilot_evidence, fresh_evidence, contracts = _validation_bundle()
    pilot, fresh, receipt = _certify_bundle(
        registry, digest, pilot_evidence, fresh_evidence, contracts,
    )
    assert pilot["pilot_gate"] is True
    assert fresh["fresh_gate"] is True
    assert receipt["difficulty_certification_gate"] is True
    assert pilot["paper_commit"] == fresh["paper_commit"] == receipt["paper_commit"] == PAPER_COMMIT
    assert pilot["validation_registry_digest"] == fresh["validation_registry_digest"] == digest
    assert pilot["base_contract_registry_digest"] == fresh["base_contract_registry_digest"] == receipt["base_contract_registry_digest"]
    assert pilot["observation_ids"] == sorted(row["observation_id"] for row in registry["pilot_roster"])
    assert fresh["observation_ids"] == sorted(row["observation_id"] for row in registry["fresh_roster"])
    assert pilot["formal_gates"] == fresh["formal_gates"] == receipt["formal_gates"] == difficulty.FORMAL_GATES

    missing = certify_pilot_sweeps(None, contracts, registry, digest)
    assert missing["pilot_gate"] is False
    assert missing["evidence_digest"] is None
    assert missing["observation_ids"] == []


def test_pilot_evidence_cannot_omit_adverse_implementation_or_sweep() -> None:
    registry, digest, pilot_evidence, _, contracts = _validation_bundle()
    adverse = copy.deepcopy(pilot_evidence)
    by_id = {row["observation_id"]: row for row in adverse["observations"]}
    for roster_row in registry["pilot_roster"]:
        if roster_row["implementation_id"] == "pilot-alternate":
            by_id[roster_row["observation_id"]]["successes"] = (5, 30, 70, 95)[roster_row["bin"]]
    adverse_report = certify_pilot_sweeps(adverse, contracts, registry, digest)
    assert adverse_report["pilot_gate"] is False
    assert any(
        item["direction"] == "significant_increase"
        for component in adverse_report["components"].values()
        for item in component["implementations"]
    )

    omitted_implementation = copy.deepcopy(adverse)
    omitted_implementation["observations"] = [
        row for row in omitted_implementation["observations"]
        if ":pilot-alternate:" not in row["observation_id"]
    ]
    with pytest.raises(DifficultyContractError, match="exactly match"):
        certify_pilot_sweeps(omitted_implementation, contracts, registry, digest)

    missing_sweep = copy.deepcopy(pilot_evidence)
    missing_sweep["observations"] = [
        row for row in missing_sweep["observations"]
        if not row["observation_id"].startswith("pilot:pilot-reference:hazard:")
    ]
    with pytest.raises(DifficultyContractError, match="exactly match"):
        certify_pilot_sweeps(missing_sweep, contracts, registry, digest)


def test_observation_only_evidence_rejects_additions_relabelling_and_role_flags() -> None:
    registry, digest, pilot_evidence, _, contracts = _validation_bundle()
    added = copy.deepcopy(pilot_evidence)
    added["observations"].append({"observation_id": "pilot:unregistered", "successes": 1, "trials": 1})
    with pytest.raises(DifficultyContractError, match="exactly match"):
        certify_pilot_sweeps(added, contracts, registry, digest)

    relabelled = copy.deepcopy(pilot_evidence)
    relabelled["observations"][0]["observation_id"] = "pilot:candidate-system:dependency_depth:0:instance-0"
    with pytest.raises(DifficultyContractError, match="exactly match"):
        certify_pilot_sweeps(relabelled, contracts, registry, digest)

    role_flag = copy.deepcopy(pilot_evidence)
    role_flag["observations"][0]["candidate"] = False
    with pytest.raises(DifficultyContractError, match="schema mismatch"):
        certify_pilot_sweeps(role_flag, contracts, registry, digest)


def test_fresh_roster_rejects_missing_tier_instance_and_reports_unordered() -> None:
    registry, digest, _, fresh_evidence, contracts = _validation_bundle()
    missing_instance = copy.deepcopy(fresh_evidence)
    missing_instance["observations"] = [
        row for row in missing_instance["observations"]
        if row["observation_id"] != "fresh:production:P1:pilot-reference:4:fresh-1"
    ]
    with pytest.raises(DifficultyContractError, match="exactly match"):
        certify_fresh_ordering(missing_instance, contracts, registry, digest)

    missing_tier = copy.deepcopy(fresh_evidence)
    missing_tier["observations"] = [
        row for row in missing_tier["observations"] if ":4:" not in row["observation_id"]
    ]
    with pytest.raises(DifficultyContractError, match="exactly match"):
        certify_fresh_ordering(missing_tier, contracts, registry, digest)

    unordered = copy.deepcopy(fresh_evidence)
    for row in unordered["observations"]:
        if ":pilot-reference:3:" in row["observation_id"]:
            row["successes"] = 80
    report = certify_fresh_ordering(unordered, contracts, registry, digest)
    assert report["fresh_gate"] is False
    assert any(row["lower_tier"] == 2 for row in report["unordered_pairs"])


def test_registry_and_base_contract_replacement_cannot_be_resigned_under_old_pin() -> None:
    registry, digest, pilot_evidence, _, contracts = _validation_bundle()
    replacement = copy.deepcopy(registry)
    replacement["implementations"] = [
        row for row in replacement["implementations"] if row["implementation_id"] != "pilot-alternate"
    ]
    replacement["pilot_roster"] = [
        row for row in replacement["pilot_roster"] if row["implementation_id"] != "pilot-alternate"
    ]
    replacement["fresh_roster"] = [
        row for row in replacement["fresh_roster"] if row["implementation_id"] != "pilot-alternate"
    ]
    assert validation_registry_digest(replacement) != digest
    with pytest.raises(DifficultyContractError, match="externally pinned"):
        certify_pilot_sweeps(pilot_evidence, contracts, replacement, digest)

    missing_contract = dict(contracts)
    missing_contract.pop(next(iter(missing_contract)))
    with pytest.raises(DifficultyContractError, match="exactly match"):
        certify_pilot_sweeps(pilot_evidence, missing_contract, registry, digest)

    extra_contract = dict(contracts)
    unrelated = copy.deepcopy(_base_contract())
    unrelated["hidden_variables"] = [{"variable_id": "extra", "candidate_set": ["a", "b"]}]
    unrelated = bind_base_contract(unrelated)
    extra_contract[unrelated["base_contract_hash"]] = unrelated
    with pytest.raises(DifficultyContractError, match="exactly match"):
        certify_pilot_sweeps(pilot_evidence, extra_contract, registry, digest)


def test_final_receipt_binds_reports_evidence_and_reproduces_distinct_packages() -> None:
    registry, digest, pilot_evidence, fresh_evidence, contracts = _validation_bundle()
    pilot, fresh, first = _certify_bundle(
        registry, digest, pilot_evidence, fresh_evidence, contracts,
    )
    assert validate_certification(
        first, pilot_evidence, fresh_evidence, contracts, registry, digest,
    ) == first

    for phase in ("pilot", "fresh"):
        altered_pilot = copy.deepcopy(pilot)
        altered_fresh = copy.deepcopy(fresh)
        if phase == "pilot":
            altered_pilot["report_digest"] = "0" * 64
        else:
            altered_fresh["report_digest"] = "0" * 64
        with pytest.raises(DifficultyContractError, match=f"{phase} report does not reproduce"):
            difficulty_certification(
                altered_pilot, altered_fresh, pilot_evidence, fresh_evidence,
                contracts, registry, digest,
            )

    forged = copy.deepcopy(first)
    forged["difficulty_certification_gate"] = False
    forged["certification_digest"] = _digest({
        key: value for key, value in forged.items() if key != "certification_digest"
    })
    with pytest.raises(DifficultyContractError, match="does not reproduce"):
        validate_certification(
            forged, pilot_evidence, fresh_evidence, contracts, registry, digest,
        )

    second_pilot_evidence = copy.deepcopy(pilot_evidence)
    second_pilot_evidence["observations"][0]["successes"] = 94
    second_pilot, second_fresh, second = _certify_bundle(
        registry, digest, second_pilot_evidence, fresh_evidence, contracts,
    )
    assert second_pilot["pilot_gate"] is True
    assert second_fresh["fresh_gate"] is True
    assert second["difficulty_certification_gate"] is True
    assert second["pilot_evidence_digest"] != first["pilot_evidence_digest"]
    assert second["pilot_report_digest"] != first["pilot_report_digest"]
    assert second["certification_digest"] != first["certification_digest"]
    assert validate_certification(
        second, second_pilot_evidence, fresh_evidence, contracts, registry, digest,
    ) == second
    assert first["formal_gates"] == second["formal_gates"] == difficulty.FORMAL_GATES
