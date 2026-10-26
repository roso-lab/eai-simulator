"""Registered D4 response and work-confirmation first-event diagnostics.

The current paper defines two independent competing-risk processes over the
same event-eligible trajectories.  This module validates their frozen
registration and evaluator-owned observations, reduces each process, and
produces a replayable diagnostic package.  It deliberately does not compute or
modify the primary D4 value score.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Mapping, Sequence

from .records_v3 import canonical_digest
from .statistics_v3 import competing_risks


REGISTRATION_SCHEMA = "heterocoord.d4-first-event-registration.v1"
OBSERVATION_SCHEMA = "heterocoord.d4-first-event-observation.v1"
PACKAGE_SCHEMA = "heterocoord.d4-first-event-package.v1"
VERSION = "d4-first-event-v1"
PLAN_KEY = "d4_first_event_registration"
FACT_KEY = "d4_first_event_observation"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_CAUSES = frozenset({"fail", "infeas", "safe"})
_PRIORITY = {"fail": 0, "infeas": 1, "safe": 2, "resp": 3, "resume": 3}
_PROCESS_CAUSES = {
    "response": ("resp", "fail", "safe", "infeas"),
    "work_confirmation": ("resume", "fail", "safe", "infeas"),
}


class D4FirstEventError(ValueError):
    """Raised when the frozen first-event evidence cannot be evaluated."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise D4FirstEventError(f"{name} must be an object")
    return value


def _exact(value: Mapping[str, Any], required: set[str], name: str, optional: set[str] | None = None) -> None:
    allowed = required | (optional or set())
    if not required.issubset(value) or set(value) - allowed:
        raise D4FirstEventError(f"{name} schema mismatch")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise D4FirstEventError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if not _DIGEST_RE.fullmatch(value):
        raise D4FirstEventError(f"{name} must be a lowercase SHA-256")
    return value


def _tick(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise D4FirstEventError(f"{name} must be a non-negative integer tick")
    return value


def _positive_tick(value: Any, name: str) -> int:
    result = _tick(value, name)
    if result == 0:
        raise D4FirstEventError(f"{name} must be positive")
    return result


def _weight(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise D4FirstEventError(f"{name} must be a finite weight")
    result = float(value)
    if result <= 0.0 or result > 1.0:
        raise D4FirstEventError(f"{name} must lie in (0,1]")
    return result


def _validate_registration(value: Mapping[str, Any]) -> dict[str, Any]:
    value = _mapping(value, "D4 first-event registration")
    _exact(value, {"schema", "version", "panels", "registration_digest"}, "D4 first-event registration")
    if value["schema"] != REGISTRATION_SCHEMA or value["version"] != VERSION:
        raise D4FirstEventError("unsupported D4 first-event registration")
    panels = value["panels"]
    if not isinstance(panels, list) or not panels:
        raise D4FirstEventError("D4 first-event registration panels must be non-empty")
    detached_panels: list[dict[str, Any]] = []
    panel_ids: set[str] = set()
    registration_ids: set[str] = set()
    # One sealed receipt identity can carry exactly one first-event
    # observation.  Do not permit the same trajectory to be registered in
    # multiple panels/cells: that would either reuse one observation twice or
    # require an ambiguous list-valued fact that the receipt schema does not
    # define.
    trajectory_keys: set[tuple[str, str, str, str]] = set()
    for panel_index, raw_panel in enumerate(panels):
        panel = _mapping(raw_panel, f"registration.panels[{panel_index}]")
        _exact(
            panel,
            {
                "panel_id", "registration_id", "system_id", "event_id", "condition_id", "onset_tick",
                "horizon_tick", "evaluator_stride_ticks", "cells",
            },
            f"registration.panels[{panel_index}]",
        )
        panel_id = _text(panel["panel_id"], f"registration.panels[{panel_index}].panel_id")
        if panel_id in panel_ids:
            raise D4FirstEventError(f"duplicate D4 first-event panel_id: {panel_id}")
        panel_ids.add(panel_id)
        registration_id = _text(
            panel["registration_id"],
            f"registration.panels[{panel_index}].registration_id",
        )
        if registration_id in registration_ids:
            raise D4FirstEventError(
                f"duplicate D4 first-event registration_id: {registration_id}"
            )
        registration_ids.add(registration_id)
        system_id = _text(panel["system_id"], f"registration.panels[{panel_index}].system_id")
        event_id = _text(panel["event_id"], f"registration.panels[{panel_index}].event_id")
        condition_id = _text(panel["condition_id"], f"registration.panels[{panel_index}].condition_id")
        onset = _tick(panel["onset_tick"], f"registration.panels[{panel_index}].onset_tick")
        horizon = _tick(panel["horizon_tick"], f"registration.panels[{panel_index}].horizon_tick")
        if horizon <= onset:
            raise D4FirstEventError("D4 first-event horizon must be after onset")
        stride = _positive_tick(panel["evaluator_stride_ticks"], f"registration.panels[{panel_index}].evaluator_stride_ticks")
        cells = panel["cells"]
        if not isinstance(cells, list) or not cells:
            raise D4FirstEventError(f"registration panel {panel_id} needs cells")
        cell_ids: set[str] = set()
        detached_cells: list[dict[str, Any]] = []
        weight_sum = 0.0
        for cell_index, raw_cell in enumerate(cells):
            cell_name = f"registration.panels[{panel_index}].cells[{cell_index}]"
            cell = _mapping(raw_cell, cell_name)
            _exact(
                cell,
                {"cell_id", "weight", "response_required", "response_deadline_tick", "expected_trajectories"},
                cell_name,
            )
            cell_id = _text(cell["cell_id"], f"{cell_name}.cell_id")
            if cell_id in cell_ids:
                raise D4FirstEventError(f"duplicate D4 first-event cell_id in {panel_id}: {cell_id}")
            cell_ids.add(cell_id)
            cell_weight = _weight(cell["weight"], f"{cell_name}.weight")
            weight_sum += cell_weight
            required = cell["response_required"]
            if type(required) is not bool:
                raise D4FirstEventError(f"{cell_name}.response_required must be boolean")
            deadline = cell["response_deadline_tick"]
            if required:
                deadline = _tick(deadline, f"{cell_name}.response_deadline_tick")
                if not onset <= deadline <= horizon:
                    raise D4FirstEventError("required response deadline must lie in the registered window")
            elif deadline is not None:
                raise D4FirstEventError("unnecessary response must not have a deadline")
            roster = cell["expected_trajectories"]
            if not isinstance(roster, list) or not roster:
                raise D4FirstEventError(f"{cell_name}.expected_trajectories must be non-empty")
            detached_roster: list[dict[str, str]] = []
            local_roster: set[tuple[str, str]] = set()
            for trajectory_index, raw_trajectory in enumerate(roster):
                trajectory_name = f"{cell_name}.expected_trajectories[{trajectory_index}]"
                trajectory = _mapping(raw_trajectory, trajectory_name)
                _exact(trajectory, {"base_instance_id", "repeat_id"}, trajectory_name)
                base = _text(trajectory["base_instance_id"], f"{trajectory_name}.base_instance_id")
                repeat = _text(trajectory["repeat_id"], f"{trajectory_name}.repeat_id")
                local_key = (base, repeat)
                if local_key in local_roster:
                    raise D4FirstEventError(f"duplicate D4 first-event trajectory in {panel_id}/{cell_id}")
                local_roster.add(local_key)
                global_key = (system_id, condition_id, base, repeat)
                if global_key in trajectory_keys:
                    raise D4FirstEventError("duplicate D4 first-event registered trajectory")
                trajectory_keys.add(global_key)
                detached_roster.append({"base_instance_id": base, "repeat_id": repeat})
            detached_cells.append(
                {
                    "cell_id": cell_id,
                    "weight": cell_weight,
                    "response_required": required,
                    "response_deadline_tick": deadline,
                    "expected_trajectories": detached_roster,
                }
            )
        if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise D4FirstEventError(f"D4 first-event cell weights for {panel_id} must sum to one")
        detached_panels.append(
            {
                "panel_id": panel_id,
                "registration_id": registration_id,
                "system_id": system_id,
                "event_id": event_id,
                "condition_id": condition_id,
                "onset_tick": onset,
                "horizon_tick": horizon,
                "evaluator_stride_ticks": stride,
                "cells": detached_cells,
            }
        )
    detached = {"schema": REGISTRATION_SCHEMA, "version": VERSION, "panels": detached_panels}
    expected_digest = canonical_digest(detached)
    if _digest(value["registration_digest"], "registration.registration_digest") != expected_digest:
        raise D4FirstEventError("D4 first-event registration digest mismatch")
    return {**detached, "registration_digest": expected_digest}


def validate_registration(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and JSON-detach a frozen registration."""

    return deepcopy(_validate_registration(value))


def registration_condition_ids(plan: Mapping[str, Any]) -> frozenset[str]:
    """Return the exact additional receipt conditions admitted by a plan."""

    plan = _mapping(plan, "plan")
    registration = plan.get(PLAN_KEY)
    if registration is None:
        return frozenset()
    validated = _validate_registration(_mapping(registration, f"plan.{PLAN_KEY}"))
    return frozenset(panel["condition_id"] for panel in validated["panels"])


def _event(value: Any, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    value = _mapping(value, name)
    _exact(value, {"tick", "evidence_digest"}, name)
    return {"tick": _tick(value["tick"], f"{name}.tick"), "evidence_digest": _digest(value["evidence_digest"], f"{name}.evidence_digest")}


def _terminal(value: Any, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    value = _mapping(value, name)
    _exact(value, {"cause", "tick", "evidence_digest", "certified", "policy_exhaustion"}, name)
    cause = value["cause"]
    if cause not in _TERMINAL_CAUSES:
        raise D4FirstEventError(f"{name}.cause is unsupported")
    if type(value["certified"]) is not bool or type(value["policy_exhaustion"]) is not bool:
        raise D4FirstEventError(f"{name} certification fields must be boolean")
    return {
        "cause": cause,
        "tick": _tick(value["tick"], f"{name}.tick"),
        "evidence_digest": _digest(value["evidence_digest"], f"{name}.evidence_digest"),
        "certified": value["certified"],
        "policy_exhaustion": value["policy_exhaustion"],
    }


def _safety(value: Any, name: str) -> dict[str, Any]:
    value = _mapping(value, name)
    _exact(value, {"flag", "evidence_digest"}, name)
    if type(value["flag"]) is not bool:
        raise D4FirstEventError(f"{name}.flag must be boolean")
    evidence = value["evidence_digest"]
    if value["flag"]:
        evidence = _digest(evidence, f"{name}.evidence_digest")
    elif evidence is not None:
        raise D4FirstEventError(f"{name}.evidence_digest must be null when flag=false")
    return {"flag": value["flag"], "evidence_digest": evidence}


def _validate_observation(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = _mapping(value, name)
    _exact(
        value,
        {
            "schema", "registration_id", "panel_id", "cell_id", "event_id", "event_eligible",
            "eligibility_evidence_digest", "response", "work_confirmation", "terminal",
            "safety", "observed_through_tick", "timeline_digest",
        },
        name,
    )
    if value["schema"] != OBSERVATION_SCHEMA:
        raise D4FirstEventError(f"{name}.schema is unsupported")
    eligible = value["event_eligible"]
    if type(eligible) is not bool:
        raise D4FirstEventError(f"{name}.event_eligible must be boolean")
    response = _event(value["response"], f"{name}.response")
    work = _event(value["work_confirmation"], f"{name}.work_confirmation")
    terminal = _terminal(value["terminal"], f"{name}.terminal")
    safety = _safety(value["safety"], f"{name}.safety")
    if not eligible and any(item is not None for item in (response, work, terminal)):
        raise D4FirstEventError("event-ineligible observation cannot contain process events")
    if terminal is not None:
        for event_name, event in (("response", response), ("work_confirmation", work)):
            if event is not None and event["tick"] > terminal["tick"]:
                raise D4FirstEventError(
                    f"{event_name} cannot occur after irreversible terminal cause"
                )
    if safety["flag"] and terminal is None:
        raise D4FirstEventError("safety flag requires an episode termination")
    if terminal is not None and terminal["cause"] == "safe" and not safety["flag"]:
        raise D4FirstEventError("terminal safe cause requires the separate safety flag")
    return {
        "schema": OBSERVATION_SCHEMA,
        "registration_id": _text(value["registration_id"], f"{name}.registration_id"),
        "panel_id": _text(value["panel_id"], f"{name}.panel_id"),
        "cell_id": _text(value["cell_id"], f"{name}.cell_id"),
        "event_id": _text(value["event_id"], f"{name}.event_id"),
        "event_eligible": eligible,
        "eligibility_evidence_digest": _digest(value["eligibility_evidence_digest"], f"{name}.eligibility_evidence_digest"),
        "response": response,
        "work_confirmation": work,
        "terminal": terminal,
        "safety": safety,
        "observed_through_tick": _tick(value["observed_through_tick"], f"{name}.observed_through_tick"),
        "timeline_digest": _digest(value["timeline_digest"], f"{name}.timeline_digest"),
    }


def _registration_index(registration: Mapping[str, Any]) -> dict[tuple[str, str, str, str, str, str], tuple[Mapping[str, Any], Mapping[str, Any]]]:
    result = {}
    for panel in registration["panels"]:
        for cell in panel["cells"]:
            for trajectory in cell["expected_trajectories"]:
                key = (
                    panel["system_id"], panel["condition_id"], trajectory["base_instance_id"],
                    trajectory["repeat_id"], panel["panel_id"], cell["cell_id"],
                )
                result[key] = (panel, cell)
    return result


def _normalized_observations_from_receipts(
    receipts: Sequence[Mapping[str, Any]], registration: Mapping[str, Any]
) -> list[dict[str, Any]]:
    index = _registration_index(registration)
    found: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for receipt_index, receipt in enumerate(receipts):
        receipt = _mapping(receipt, f"receipts[{receipt_index}]")
        identity = _mapping(receipt.get("identity"), f"receipts[{receipt_index}].identity")
        facts = _mapping(receipt.get("facts"), f"receipts[{receipt_index}].facts")
        raw_observation = facts.get(FACT_KEY)
        if raw_observation is None:
            continue
        observation = _validate_observation(_mapping(raw_observation, FACT_KEY), f"receipts[{receipt_index}].facts.{FACT_KEY}")
        key = (
            _text(identity.get("system_id"), "identity.system_id"),
            _text(identity.get("condition_id"), "identity.condition_id"),
            _text(identity.get("base_instance_id"), "identity.base_instance_id"),
            _text(identity.get("repeat_id"), "identity.repeat_id"),
            observation["panel_id"], observation["cell_id"],
        )
        if key not in index:
            raise D4FirstEventError("D4 first-event observation is not in the frozen trajectory roster")
        panel, _cell = index[key]
        if observation["event_id"] != panel["event_id"]:
            raise D4FirstEventError("D4 first-event observation event identity mismatch")
        if observation["registration_id"] != panel["registration_id"]:
            raise D4FirstEventError("D4 first-event observation registration identity mismatch")
        if key in found:
            raise D4FirstEventError("duplicate D4 first-event observation for a registered trajectory")
        found[key] = {
            "system_id": key[0],
            "condition_id": key[1],
            "base_instance_id": key[2],
            "repeat_id": key[3],
            "source_receipt_digest": canonical_digest(receipt),
            "observation": observation,
        }
    missing = sorted(set(index) - set(found))
    if missing:
        raise D4FirstEventError(f"D4 first-event exact roster is incomplete: {missing}")
    return [found[key] for key in sorted(found)]


def _classify_terminal(terminal: Mapping[str, Any] | None) -> tuple[str | None, dict[str, Any] | None]:
    if terminal is None:
        return None, None
    cause = terminal["cause"]
    note = None
    if terminal["policy_exhaustion"]:
        cause = "fail"
        note = "policy_exhaustion_is_method_failure"
    elif cause in {"infeas", "safe"} and not terminal["certified"]:
        raise D4FirstEventError(f"terminal {cause} requires evaluator certification")
    record = {"kind": "terminal", "cause": cause, "tick": terminal["tick"],
              "evidence_digest": terminal["evidence_digest"], "note": note,
              "declared_cause": terminal["cause"]}
    return cause, record


def _trajectory_process(
    *,
    panel: Mapping[str, Any],
    cell: Mapping[str, Any],
    row: Mapping[str, Any],
    process: str,
) -> tuple[tuple[int, str | None], dict[str, Any]]:
    observation = row["observation"]
    onset = panel["onset_tick"]
    horizon = panel["horizon_tick"]
    observed_through = observation["observed_through_tick"]
    if observed_through < onset:
        raise D4FirstEventError("D4 first-event observation ends before onset")
    for name in ("response", "work_confirmation", "terminal"):
        event = observation[name]
        if event is not None:
            if not onset <= event["tick"] <= horizon:
                raise D4FirstEventError(f"D4 first-event {name} lies outside the registered window")
            if event["tick"] > observed_through:
                raise D4FirstEventError(f"D4 first-event {name} exceeds observation coverage")
    _terminal_cause, terminal_record = _classify_terminal(observation["terminal"])
    records: list[dict[str, Any]] = []
    if terminal_record is not None:
        records.append(terminal_record)
    event_name = "response" if process == "response" else "work_confirmation"
    process_cause = "resp" if process == "response" else "resume"
    process_event = observation[event_name]
    if process_event is not None:
        records.append({"kind": event_name, "cause": process_cause, "tick": process_event["tick"],
                        "evidence_digest": process_event["evidence_digest"], "note": None})
    if process == "response" and cell["response_required"]:
        deadline = cell["response_deadline_tick"]
        assert deadline is not None
        if process_event is None or process_event["tick"] > deadline:
            records.append({"kind": "required_response_deadline", "cause": "fail", "tick": deadline,
                            "evidence_digest": observation["timeline_digest"],
                            "note": "required_response_absent_at_deadline"})
    records.sort(key=lambda item: (item["tick"], _PRIORITY[item["cause"]], item["kind"], item["evidence_digest"]))
    if records:
        selected_tick = records[0]["tick"]
        tied = [item for item in records if item["tick"] == selected_tick]
        selected = min(tied, key=lambda item: _PRIORITY[item["cause"]])
        if observed_through < selected_tick:
            raise D4FirstEventError("D4 first-event coverage does not reach the selected event")
        cause: str | None = selected["cause"]
        absolute_tick = selected_tick
    else:
        if observed_through < horizon:
            raise D4FirstEventError("event-free D4 trajectory lacks coverage through the common horizon")
        cause = None
        absolute_tick = horizon
        tied = []
    elapsed = absolute_tick - onset
    detail = {
        "base_instance_id": row["base_instance_id"],
        "repeat_id": row["repeat_id"],
        "process": process,
        "selected_tick": absolute_tick,
        "elapsed_tick": elapsed,
        "cause": cause or "censor",
        "tied_causes": sorted({item["cause"] for item in tied}, key=lambda item: _PRIORITY[item]),
        "tied_cause_counts": {
            item_cause: sum(item["cause"] == item_cause for item in tied)
            for item_cause in _PROCESS_CAUSES[process]
        },
        "underlying_events": records,
        "safety": observation["safety"],
        "timeline_digest": observation["timeline_digest"],
    }
    return (elapsed, cause), detail


def _fixed_process(trajectories: Sequence[tuple[int, str | None]], *, horizon: int, process: str) -> dict[str, Any]:
    calculated = competing_risks(trajectories, horizon=horizon)
    event_causes = _PROCESS_CAUSES[process]
    counts = {cause: sum(1 for _tick_value, observed in trajectories if observed == cause) for cause in event_causes}
    counts["censor"] = sum(1 for _tick_value, observed in trajectories if observed is None)
    cif = {cause: float(calculated["cif"].get(cause, 0.0)) for cause in event_causes}
    curve = []
    for point in calculated["curve"]:
        curve.append({
            "time": point["time"],
            "survival": point["survival"],
            "cif": {cause: float(point["cif"].get(cause, 0.0)) for cause in event_causes},
            "risk_after": point["risk_after"],
        })
    return {
        "cause_directory": [*event_causes, "censor"],
        "trajectory_count": len(trajectories),
        "cause_counts": counts,
        "censor_count": counts["censor"],
        "cif": cif,
        "event_free_survival": calculated["event_free_survival"],
        "rmst": calculated["rmst"],
        "status": calculated["status"],
        "curve": curve,
    }


def _empty_process(*, process: str) -> dict[str, Any]:
    """Represent a registered cell with no event-eligible trajectories.

    Event ineligibility is a valid exclusion-funnel outcome, not malformed
    evidence.  The cell and therefore its panel remain non-estimable without
    redistributing the frozen weight.
    """

    event_causes = _PROCESS_CAUSES[process]
    return {
        "cause_directory": [*event_causes, "censor"],
        "trajectory_count": 0,
        "cause_counts": {cause: 0 for cause in (*event_causes, "censor")},
        "censor_count": 0,
        "cif": {cause: None for cause in event_causes},
        "event_free_survival": None,
        "rmst": None,
        "status": "event_support_absent",
        "curve": [],
    }


def _curve_state(process: Mapping[str, Any], tick: int, causes: Sequence[str]) -> tuple[float, dict[str, float]]:
    survival = 1.0
    cif = {cause: 0.0 for cause in causes}
    for point in process["curve"]:
        if point["time"] > tick:
            break
        survival = float(point["survival"])
        cif = {cause: float(point["cif"][cause]) for cause in causes}
    return survival, cif


def _aggregate_panel(cells: Sequence[Mapping[str, Any]], *, process: str, horizon: int) -> dict[str, Any]:
    causes = _PROCESS_CAUSES[process]
    if any(cell[process]["status"] != "valid" for cell in cells):
        return {
            "cause_directory": [*causes, "censor"],
            "trajectory_count": sum(cell[process]["trajectory_count"] for cell in cells),
            "cause_counts": {
                cause: sum(cell[process]["cause_counts"][cause] for cell in cells)
                for cause in (*causes, "censor")
            },
            "censor_count": sum(cell[process]["censor_count"] for cell in cells),
            "cif": {cause: None for cause in causes},
            "event_free_survival": None,
            "rmst": None,
            "status": "event_support_absent",
            "horizon_elapsed_ticks": horizon,
            "curve": [],
        }
    times = sorted({point["time"] for cell in cells for point in cell[process]["curve"]})
    curve = []
    for tick in times:
        survival = 0.0
        cif = {cause: 0.0 for cause in causes}
        for cell in cells:
            cell_survival, cell_cif = _curve_state(cell[process], tick, causes)
            survival += cell["weight"] * cell_survival
            for cause in causes:
                cif[cause] += cell["weight"] * cell_cif[cause]
        curve.append({"time": tick, "survival": survival, "cif": cif})
    return {
        "cause_directory": [*causes, "censor"],
        "trajectory_count": sum(cell[process]["trajectory_count"] for cell in cells),
        "cause_counts": {
            cause: sum(cell[process]["cause_counts"][cause] for cell in cells)
            for cause in (*causes, "censor")
        },
        "censor_count": sum(cell[process]["censor_count"] for cell in cells),
        "cif": {cause: sum(cell["weight"] * cell[process]["cif"][cause] for cell in cells) for cause in causes},
        "event_free_survival": sum(cell["weight"] * cell[process]["event_free_survival"] for cell in cells),
        "rmst": sum(cell["weight"] * cell[process]["rmst"] for cell in cells),
        "status": "valid" if all(cell[process]["status"] == "valid" for cell in cells) else "incomplete",
        "horizon_elapsed_ticks": horizon,
        "curve": curve,
    }


def _assemble(registration: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    observation_index = {
        (
            row["system_id"], row["condition_id"], row["base_instance_id"], row["repeat_id"],
            row["observation"]["panel_id"], row["observation"]["cell_id"],
        ): row
        for row in observations
    }
    registered_keys = set(_registration_index(registration))
    observed_keys = set(observation_index)
    if observed_keys != registered_keys:
        missing = sorted(registered_keys - observed_keys)
        extra = sorted(observed_keys - registered_keys)
        raise D4FirstEventError(
            f"D4 first-event package exact roster mismatch (missing={missing}, extra={extra})"
        )
    panels_out: list[dict[str, Any]] = []
    for panel in registration["panels"]:
        horizon = panel["horizon_tick"] - panel["onset_tick"]
        cells_out: list[dict[str, Any]] = []
        for cell in panel["cells"]:
            rows = []
            for trajectory in cell["expected_trajectories"]:
                key = (
                    panel["system_id"], panel["condition_id"], trajectory["base_instance_id"],
                    trajectory["repeat_id"], panel["panel_id"], cell["cell_id"],
                )
                if key not in observation_index:
                    raise D4FirstEventError("D4 first-event package exact roster is incomplete")
                rows.append(observation_index[key])
            eligible = [row for row in rows if row["observation"]["event_eligible"]]
            excluded = [row for row in rows if not row["observation"]["event_eligible"]]
            response_trajectories = []
            response_details = []
            work_trajectories = []
            work_details = []
            for row in eligible:
                trajectory, detail = _trajectory_process(panel=panel, cell=cell, row=row, process="response")
                response_trajectories.append(trajectory)
                response_details.append(detail)
                trajectory, detail = _trajectory_process(panel=panel, cell=cell, row=row, process="work_confirmation")
                work_trajectories.append(trajectory)
                work_details.append(detail)
            cells_out.append(
                {
                    "cell_id": cell["cell_id"],
                    "weight": cell["weight"],
                    "expected_trajectory_count": len(rows),
                    "eligible_trajectory_count": len(eligible),
                    "excluded_trajectory_count": len(excluded),
                    "excluded_trajectories": [
                        {
                            "base_instance_id": row["base_instance_id"],
                            "repeat_id": row["repeat_id"],
                            "eligibility_evidence_digest": row["observation"]["eligibility_evidence_digest"],
                        }
                        for row in excluded
                    ],
                    "response": (
                        _fixed_process(response_trajectories, horizon=horizon, process="response")
                        if response_trajectories else _empty_process(process="response")
                    ),
                    "work_confirmation": (
                        _fixed_process(work_trajectories, horizon=horizon, process="work_confirmation")
                        if work_trajectories else _empty_process(process="work_confirmation")
                    ),
                    "response_trajectories": response_details,
                    "work_confirmation_trajectories": work_details,
                }
            )
        panels_out.append(
            {
                "panel_id": panel["panel_id"],
                "registration_id": panel["registration_id"],
                "system_id": panel["system_id"],
                "event_id": panel["event_id"],
                "condition_id": panel["condition_id"],
                "onset_tick": panel["onset_tick"],
                "horizon_tick": panel["horizon_tick"],
                "horizon_elapsed_ticks": horizon,
                "evaluator_stride_ticks": panel["evaluator_stride_ticks"],
                "cells": cells_out,
                "response": _aggregate_panel(cells_out, process="response", horizon=horizon),
                "work_confirmation": _aggregate_panel(cells_out, process="work_confirmation", horizon=horizon),
            }
        )
    base = {
        "schema": PACKAGE_SCHEMA,
        "version": VERSION,
        "registration": deepcopy(registration),
        "registration_digest": registration["registration_digest"],
        "observations": deepcopy(list(observations)),
        "source_receipt_digests": sorted(row["source_receipt_digest"] for row in observations),
        "panels": panels_out,
        "formal_gates": {"rankable": False, "paper_score_eligible": False, "physical_qualified": False},
    }
    return {**base, "package_digest": canonical_digest(base)}


def reduce_receipts(receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Reduce registered receipt facts, or return ``None`` for legacy input."""

    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes)) or not receipts:
        raise D4FirstEventError("receipts must be a non-empty sequence")
    first = _mapping(receipts[0], "receipts[0]")
    plan = _mapping(first.get("plan"), "receipts[0].plan")
    raw_registration = plan.get(PLAN_KEY)
    has_observation = any(
        isinstance(row, Mapping) and isinstance(row.get("facts"), Mapping) and FACT_KEY in row["facts"]
        for row in receipts
    )
    if raw_registration is None:
        if has_observation:
            raise D4FirstEventError("D4 first-event observation has no frozen registration")
        return None
    registration = _validate_registration(_mapping(raw_registration, f"plan.{PLAN_KEY}"))
    for index, receipt in enumerate(receipts[1:], start=1):
        other_plan = _mapping(_mapping(receipt, f"receipts[{index}]").get("plan"), f"receipts[{index}].plan")
        if other_plan.get(PLAN_KEY) != raw_registration:
            raise D4FirstEventError("all receipts must share the exact D4 first-event registration")
    observations = _normalized_observations_from_receipts(receipts, registration)
    return _assemble(registration, observations)


def validate_package(value: Mapping[str, Any], *, receipt_digests: Sequence[str] | None = None) -> dict[str, Any]:
    """Recompute a normalized package and reject altered derived output."""

    value = _mapping(value, "D4 first-event package")
    _exact(
        value,
        {
            "schema", "version", "registration", "registration_digest", "observations",
            "source_receipt_digests", "panels", "formal_gates", "package_digest",
        },
        "D4 first-event package",
    )
    if value["schema"] != PACKAGE_SCHEMA or value["version"] != VERSION:
        raise D4FirstEventError("unsupported D4 first-event package")
    registration = _validate_registration(_mapping(value["registration"], "package.registration"))
    if value["registration_digest"] != registration["registration_digest"]:
        raise D4FirstEventError("D4 first-event package registration digest mismatch")
    observations = value["observations"]
    if not isinstance(observations, list) or not observations:
        raise D4FirstEventError("D4 first-event package observations must be non-empty")
    detached: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for index, raw_row in enumerate(observations):
        row = _mapping(raw_row, f"package.observations[{index}]")
        _exact(row, {"system_id", "condition_id", "base_instance_id", "repeat_id", "source_receipt_digest", "observation"}, f"package.observations[{index}]")
        observation = _validate_observation(_mapping(row["observation"], "observation"), f"package.observations[{index}].observation")
        item = {
            "system_id": _text(row["system_id"], "system_id"),
            "condition_id": _text(row["condition_id"], "condition_id"),
            "base_instance_id": _text(row["base_instance_id"], "base_instance_id"),
            "repeat_id": _text(row["repeat_id"], "repeat_id"),
            "source_receipt_digest": _digest(row["source_receipt_digest"], "source_receipt_digest"),
            "observation": observation,
        }
        key = (item["system_id"], item["condition_id"], item["base_instance_id"], item["repeat_id"], observation["panel_id"], observation["cell_id"])
        if key in seen:
            raise D4FirstEventError("duplicate D4 first-event package observation")
        seen.add(key)
        detached.append(item)
    recomputed = _assemble(registration, sorted(detached, key=lambda row: (row["system_id"], row["condition_id"], row["base_instance_id"], row["repeat_id"], row["observation"]["panel_id"], row["observation"]["cell_id"])))
    if receipt_digests is not None:
        supplied = list(receipt_digests)
        if any(not isinstance(item, str) or not _DIGEST_RE.fullmatch(item) for item in supplied):
            raise D4FirstEventError("receipt_digests must contain lowercase SHA-256 values")
        if not set(recomputed["source_receipt_digests"]).issubset(set(supplied)):
            raise D4FirstEventError("D4 first-event package source receipts are absent from normalized input")
    if recomputed != value:
        raise D4FirstEventError("D4 first-event package does not match deterministic replay")
    return deepcopy(recomputed)


__all__ = [
    "REGISTRATION_SCHEMA", "OBSERVATION_SCHEMA", "PACKAGE_SCHEMA", "VERSION",
    "PLAN_KEY", "FACT_KEY", "D4FirstEventError", "validate_registration",
    "registration_condition_ids", "reduce_receipts", "validate_package",
]
