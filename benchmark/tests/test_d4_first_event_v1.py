from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

from benchmark.d4_first_event_v1 import (
    D4FirstEventError,
    FACT_KEY,
    OBSERVATION_SCHEMA,
    PACKAGE_SCHEMA,
    PLAN_KEY,
    REGISTRATION_SCHEMA,
    VERSION,
    reduce_receipts,
    registration_condition_ids,
    validate_package,
    validate_registration,
)
from benchmark.records_v3 import canonical_digest


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _trajectory(base: str, repeat: str) -> dict[str, str]:
    return {"base_instance_id": base, "repeat_id": repeat}


def _registration(*, cells=None, panels=None) -> dict:
    if cells is None:
        cells = [
            {
                "cell_id": "cell-a",
                "weight": 1.0,
                "response_required": True,
                "response_deadline_tick": 130,
                "expected_trajectories": [_trajectory("base-a", "r0")],
            }
        ]
    if panels is None:
        panels = [
            {
                "panel_id": "panel-a",
                "registration_id": "registration-a",
                "system_id": "system-a",
                "event_id": "task-function-loss",
                "condition_id": "task_function_loss",
                "onset_tick": 100,
                "horizon_tick": 200,
                "evaluator_stride_ticks": 5,
                "cells": cells,
            }
        ]
    base = {
        "schema": REGISTRATION_SCHEMA,
        "version": VERSION,
        "panels": panels,
    }
    return {**base, "registration_digest": canonical_digest(base)}


def _event(tick: int, label: str) -> dict:
    return {"tick": tick, "evidence_digest": _sha(label)}


def _observation(
    *,
    registration_id="registration-a",
    panel_id="panel-a",
    cell_id="cell-a",
    event_id="task-function-loss",
    eligible=True,
    response=None,
    work=None,
    terminal=None,
    observed_through=200,
    safety=False,
) -> dict:
    return {
        "schema": OBSERVATION_SCHEMA,
        "registration_id": registration_id,
        "panel_id": panel_id,
        "cell_id": cell_id,
        "event_id": event_id,
        "event_eligible": eligible,
        "eligibility_evidence_digest": _sha(f"eligible-{panel_id}-{cell_id}"),
        "response": response,
        "work_confirmation": work,
        "terminal": terminal,
        "safety": {
            "flag": safety,
            "evidence_digest": _sha("safety") if safety else None,
        },
        "observed_through_tick": observed_through,
        "timeline_digest": _sha(f"timeline-{panel_id}-{cell_id}"),
    }


def _terminal(cause: str, tick: int, *, certified=True, policy_exhaustion=False) -> dict:
    return {
        "cause": cause,
        "tick": tick,
        "evidence_digest": _sha(f"terminal-{cause}-{tick}"),
        "certified": certified,
        "policy_exhaustion": policy_exhaustion,
    }


def _receipt(
    registration: dict,
    observation: dict | None,
    *,
    system_id="system-a",
    condition_id="task_function_loss",
    base="base-a",
    repeat="r0",
) -> dict:
    facts = {} if observation is None else {FACT_KEY: observation}
    return {
        "identity": {
            "system_id": system_id,
            "condition_id": condition_id,
            "base_instance_id": base,
            "repeat_id": repeat,
        },
        "plan": {PLAN_KEY: registration},
        "facts": facts,
    }


def _cell(package: dict, index: int = 0) -> dict:
    return package["panels"][0]["cells"][index]


def test_independent_work_confirmation_can_precede_response() -> None:
    registration = _registration()
    receipt = _receipt(
        registration,
        _observation(response=_event(125, "response"), work=_event(115, "work")),
    )
    package = reduce_receipts([receipt])
    assert package is not None
    assert _cell(package)["response_trajectories"][0]["cause"] == "resp"
    assert _cell(package)["response_trajectories"][0]["elapsed_tick"] == 25
    assert _cell(package)["work_confirmation_trajectories"][0]["cause"] == "resume"
    assert _cell(package)["work_confirmation_trajectories"][0]["elapsed_tick"] == 15


def test_exact_tick_fail_wins_both_processes_and_retains_ties() -> None:
    registration = _registration()
    observation = _observation(
        response=_event(120, "response"),
        work=_event(120, "work"),
        terminal=_terminal("fail", 120),
    )
    package = reduce_receipts([_receipt(registration, observation)])
    assert package is not None
    response = _cell(package)["response_trajectories"][0]
    work = _cell(package)["work_confirmation_trajectories"][0]
    assert response["cause"] == work["cause"] == "fail"
    assert response["tied_causes"] == ["fail", "resp"]
    assert work["tied_causes"] == ["fail", "resume"]
    assert response["tied_cause_counts"] == {
        "resp": 1,
        "fail": 1,
        "safe": 0,
        "infeas": 0,
    }
    assert work["tied_cause_counts"] == {
        "resume": 1,
        "fail": 1,
        "safe": 0,
        "infeas": 0,
    }


def test_required_response_deadline_injects_fail_and_late_response_cannot_erase_it() -> None:
    registration = _registration()
    package = reduce_receipts(
        [_receipt(registration, _observation(response=_event(131, "late-response")))]
    )
    assert package is not None
    detail = _cell(package)["response_trajectories"][0]
    assert detail["cause"] == "fail"
    assert detail["selected_tick"] == 130
    assert {item["kind"] for item in detail["underlying_events"]} == {
        "response",
        "required_response_deadline",
    }


def test_unnecessary_absent_response_and_event_free_work_are_censored() -> None:
    registration = _registration(
        cells=[
            {
                "cell_id": "cell-a",
                "weight": 1.0,
                "response_required": False,
                "response_deadline_tick": None,
                "expected_trajectories": [_trajectory("base-a", "r0")],
            }
        ]
    )
    package = reduce_receipts([_receipt(registration, _observation())])
    assert package is not None
    cell = _cell(package)
    assert cell["response"]["cause_counts"]["censor"] == 1
    assert cell["work_confirmation"]["cause_counts"]["censor"] == 1
    assert cell["response"]["status"] == "valid"
    assert cell["work_confirmation"]["status"] == "valid"


def test_policy_exhaustion_is_fail_and_certified_terminal_causes_are_retained() -> None:
    base_cells = [
        {
            "cell_id": "cell-a",
            "weight": 1.0,
            "response_required": False,
            "response_deadline_tick": None,
            "expected_trajectories": [
                _trajectory("base-a", "r0"),
                _trajectory("base-b", "r0"),
                _trajectory("base-c", "r0"),
            ],
        }
    ]
    registration = _registration(cells=base_cells)
    receipts = [
        _receipt(registration, _observation(terminal=_terminal("infeas", 110, policy_exhaustion=True))),
        _receipt(registration, _observation(terminal=_terminal("infeas", 115)), base="base-b"),
        _receipt(registration, _observation(terminal=_terminal("safe", 120), safety=True), base="base-c"),
    ]
    package = reduce_receipts(receipts)
    assert package is not None
    counts = _cell(package)["response"]["cause_counts"]
    assert counts == {"resp": 0, "fail": 1, "safe": 1, "infeas": 1, "censor": 0}
    first = _cell(package)["response_trajectories"][0]
    assert first["underlying_events"][0]["declared_cause"] == "infeas"
    assert first["underlying_events"][0]["note"] == "policy_exhaustion_is_method_failure"


@pytest.mark.parametrize("declared_cause", ["fail", "safe", "infeas"])
def test_any_policy_exhaustion_declaration_reduces_to_method_failure(declared_cause: str) -> None:
    registration = _registration()
    package = reduce_receipts([
        _receipt(
            registration,
            _observation(
                terminal=_terminal(
                    declared_cause,
                    110,
                    certified=False,
                    policy_exhaustion=True,
                ),
                safety=declared_cause == "safe",
            ),
        )
    ])
    assert package is not None
    detail = _cell(package)["response_trajectories"][0]
    assert detail["cause"] == "fail"
    assert detail["underlying_events"][0]["declared_cause"] == declared_cause
    assert detail["underlying_events"][0]["note"] == "policy_exhaustion_is_method_failure"


@pytest.mark.parametrize("cause", ["infeas", "safe"])
def test_uncertified_terminal_cause_fails_closed(cause: str) -> None:
    registration = _registration()
    receipt = _receipt(
        registration,
        _observation(
            terminal=_terminal(cause, 110, certified=False),
            safety=cause == "safe",
        ),
    )
    with pytest.raises(D4FirstEventError, match="requires evaluator certification"):
        reduce_receipts([receipt])


def test_insufficient_coverage_fails_closed_for_selected_event_and_censoring() -> None:
    registration = _registration()
    with pytest.raises(D4FirstEventError, match="exceeds observation coverage"):
        reduce_receipts(
            [_receipt(registration, _observation(response=_event(120, "response"), observed_through=119))]
        )
    unnecessary = _registration(
        cells=[
            {
                "cell_id": "cell-a",
                "weight": 1.0,
                "response_required": False,
                "response_deadline_tick": None,
                "expected_trajectories": [_trajectory("base-a", "r0")],
            }
        ]
    )
    with pytest.raises(D4FirstEventError, match="lacks coverage"):
        reduce_receipts([_receipt(unnecessary, _observation(observed_through=199))])


def test_irreversible_terminal_and_separate_safety_flag_are_consistent() -> None:
    registration = _registration()
    with pytest.raises(D4FirstEventError, match="cannot occur after irreversible terminal"):
        reduce_receipts([
            _receipt(
                registration,
                _observation(
                    response=_event(121, "impossible-response"),
                    terminal=_terminal("fail", 120),
                ),
            )
        ])
    with pytest.raises(D4FirstEventError, match="safety flag requires"):
        reduce_receipts([_receipt(registration, _observation(safety=True))])
    with pytest.raises(D4FirstEventError, match="requires the separate safety flag"):
        reduce_receipts([
            _receipt(registration, _observation(terminal=_terminal("safe", 120)))
        ])


def test_missing_extra_duplicate_and_identity_mismatch_fail_closed() -> None:
    registration = _registration(
        cells=[
            {
                "cell_id": "cell-a",
                "weight": 1.0,
                "response_required": False,
                "response_deadline_tick": None,
                "expected_trajectories": [_trajectory("base-a", "r0"), _trajectory("base-b", "r0")],
            }
        ]
    )
    good = _receipt(registration, _observation())
    with pytest.raises(D4FirstEventError, match="exact roster is incomplete"):
        reduce_receipts([good])
    duplicate = deepcopy(good)
    with pytest.raises(D4FirstEventError, match="duplicate"):
        reduce_receipts([good, duplicate])
    extra = _receipt(registration, _observation(), base="base-x")
    with pytest.raises(D4FirstEventError, match="not in the frozen trajectory roster"):
        reduce_receipts([good, extra])
    mismatch = _receipt(registration, _observation(event_id="wrong"))
    with pytest.raises(D4FirstEventError, match="event identity mismatch"):
        reduce_receipts([mismatch])
    registration_mismatch = _receipt(
        registration,
        _observation(registration_id="wrong-registration"),
    )
    with pytest.raises(D4FirstEventError, match="registration identity mismatch"):
        reduce_receipts([registration_mismatch])


def test_same_receipt_identity_cannot_be_registered_in_two_cells_or_panels() -> None:
    cells = [
        {
            "cell_id": cell,
            "weight": 0.5,
            "response_required": False,
            "response_deadline_tick": None,
            "expected_trajectories": [_trajectory("base-a", "r0")],
        }
        for cell in ("cell-a", "cell-b")
    ]
    with pytest.raises(D4FirstEventError, match="duplicate D4 first-event registered trajectory"):
        validate_registration(_registration(cells=cells))


def test_two_cell_fixed_weight_aggregation_and_zero_count_directories() -> None:
    cells = [
        {
            "cell_id": "cell-a",
            "weight": 0.25,
            "response_required": False,
            "response_deadline_tick": None,
            "expected_trajectories": [_trajectory("base-a", "r0")],
        },
        {
            "cell_id": "cell-b",
            "weight": 0.75,
            "response_required": False,
            "response_deadline_tick": None,
            "expected_trajectories": [_trajectory("base-b", "r0")],
        },
    ]
    registration = _registration(cells=cells)
    receipts = [
        _receipt(registration, _observation(response=_event(110, "response"))),
        _receipt(
            registration,
            _observation(cell_id="cell-b", terminal=_terminal("fail", 110)),
            base="base-b",
        ),
    ]
    package = reduce_receipts(receipts)
    assert package is not None
    panel = package["panels"][0]
    assert panel["response"]["cif"] == {
        "resp": pytest.approx(0.25),
        "fail": pytest.approx(0.75),
        "safe": pytest.approx(0.0),
        "infeas": pytest.approx(0.0),
    }
    assert panel["response"]["cause_directory"] == ["resp", "fail", "safe", "infeas", "censor"]
    assert panel["work_confirmation"]["cause_counts"]["censor"] == 1


def test_package_replay_rejects_derived_source_and_roster_tampering() -> None:
    registration = _registration()
    package = reduce_receipts(
        [_receipt(registration, _observation(response=_event(120, "response"), work=_event(125, "work")))]
    )
    assert package is not None
    assert validate_package(package) == package
    assert package["schema"] == PACKAGE_SCHEMA
    assert package["formal_gates"] == {
        "rankable": False,
        "paper_score_eligible": False,
        "physical_qualified": False,
    }

    tampered = deepcopy(package)
    tampered["panels"][0]["response"]["cif"]["resp"] = 0.0
    tampered["package_digest"] = canonical_digest({k: v for k, v in tampered.items() if k != "package_digest"})
    with pytest.raises(D4FirstEventError, match="deterministic replay"):
        validate_package(tampered)

    observation_tamper = deepcopy(package)
    observation_tamper["observations"][0]["observation"]["response"]["evidence_digest"] = _sha("altered-response")
    observation_tamper["package_digest"] = canonical_digest({
        key: value for key, value in observation_tamper.items()
        if key != "package_digest"
    })
    with pytest.raises(D4FirstEventError, match="deterministic replay"):
        validate_package(observation_tamper)

    extra = deepcopy(package)
    row = deepcopy(extra["observations"][0])
    row["base_instance_id"] = "base-extra"
    row["source_receipt_digest"] = _sha("extra-receipt")
    extra["observations"].append(row)
    with pytest.raises(D4FirstEventError, match="exact roster mismatch"):
        validate_package(extra)

    with pytest.raises(D4FirstEventError, match="source receipts are absent"):
        validate_package(package, receipt_digests=[_sha("unrelated")])


def test_registration_digest_and_registered_conditions_are_strict() -> None:
    registration = _registration()
    assert registration_condition_ids({PLAN_KEY: registration}) == frozenset({"task_function_loss"})
    altered = deepcopy(registration)
    altered["panels"][0]["horizon_tick"] += 1
    with pytest.raises(D4FirstEventError, match="registration digest mismatch"):
        validate_registration(altered)


def test_observation_without_registration_and_partial_registration_fail_closed() -> None:
    receipt = _receipt(_registration(), _observation())
    receipt["plan"] = {"seed": 7}
    with pytest.raises(D4FirstEventError, match="has no frozen registration"):
        reduce_receipts([receipt])
    registration = _registration()
    with pytest.raises(D4FirstEventError, match="exact roster is incomplete"):
        reduce_receipts([_receipt(registration, None)])


def test_event_ineligible_rows_are_explicit_exclusions_not_censors() -> None:
    cells = [
        {
            "cell_id": "cell-a",
            "weight": 1.0,
            "response_required": False,
            "response_deadline_tick": None,
            "expected_trajectories": [_trajectory("base-a", "r0"), _trajectory("base-b", "r0")],
        }
    ]
    registration = _registration(cells=cells)
    package = reduce_receipts(
        [
            _receipt(registration, _observation()),
            _receipt(registration, _observation(eligible=False), base="base-b"),
        ]
    )
    assert package is not None
    cell = _cell(package)
    assert cell["expected_trajectory_count"] == 2
    assert cell["eligible_trajectory_count"] == 1
    assert cell["excluded_trajectory_count"] == 1
    assert cell["response"]["trajectory_count"] == 1
    assert cell["response"]["censor_count"] == 1


def test_empty_registered_cell_makes_panel_na_without_weight_redistribution() -> None:
    cells = [
        {
            "cell_id": "cell-a",
            "weight": 0.5,
            "response_required": False,
            "response_deadline_tick": None,
            "expected_trajectories": [_trajectory("base-a", "r0")],
        },
        {
            "cell_id": "cell-b",
            "weight": 0.5,
            "response_required": False,
            "response_deadline_tick": None,
            "expected_trajectories": [_trajectory("base-b", "r0")],
        },
    ]
    registration = _registration(cells=cells)
    package = reduce_receipts(
        [
            _receipt(registration, _observation(response=_event(110, "response"))),
            _receipt(
                registration,
                _observation(cell_id="cell-b", eligible=False),
                base="base-b",
            ),
        ]
    )
    assert package is not None
    panel = package["panels"][0]
    assert panel["cells"][1]["response"]["status"] == "event_support_absent"
    assert panel["response"]["status"] == "event_support_absent"
    assert panel["response"]["cif"] == {
        "resp": None,
        "fail": None,
        "safe": None,
        "infeas": None,
    }
    assert panel["response"]["curve"] == []
