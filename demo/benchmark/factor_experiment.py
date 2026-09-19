"""Compile one-factor comparison plans without exposing episode truth.

This module is an author/evaluator boundary.  It does not choose a robot,
change a scenario, or run Isaac.  Communication plans are executable by the
protocol harness; domain and physical axes remain explicitly fixture-pending
until their live adapter exists.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .factor_matrix import build_public_factor_manifest
from .provenance import canonical_sha256
from .communication import CommunicationRuntime, validate_communication_report


_COMMUNICATION_LEVELS = {
    "immediate": {"delay_s": 0.0, "drop": False, "bytes_per_window": None},
    "delayed": {"delay_s": 2.0, "drop": False, "bytes_per_window": None},
    "dropped": {"delay_s": 0.0, "drop": True, "bytes_per_window": None},
    "bandwidth_limited": {"delay_s": 0.0, "drop": False, "bytes_per_window": 32, "window_s": 1.0},
    "stale": {"delay_s": 0.0, "drop": False, "bytes_per_window": None},
}


def _axis(scene_key: str, factor_id: str) -> dict[str, Any]:
    manifest = build_public_factor_manifest(scene_key)
    for row in manifest["axes"]:
        if row["id"] == factor_id:
            return row
    raise ValueError(f"{scene_key}: unknown factor {factor_id!r}")


def compile_factor_pair(scene_key: str, factor_id: str, control_level: str,
                        variant_level: str, *, frozen_inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Create a pair descriptor with a private condition recipe.

    ``frozen_inputs`` is hashed and retained as evaluator metadata.  It must
    contain the immutable task/scenario identity supplied by the caller; no
    episode time, reserve identity, reference assignment, or message content
    is accepted here.  This prevents a comparison plan from silently changing
    more than the selected factor.
    """
    axis = _axis(scene_key, factor_id)
    levels = set(axis["levels"])
    if control_level not in levels or variant_level not in levels:
        raise ValueError(f"{factor_id}: pair levels must be from the reviewed axis")
    if control_level == variant_level:
        raise ValueError("factor pair must change exactly one level")
    if not isinstance(frozen_inputs, Mapping) or not frozen_inputs:
        raise ValueError("factor pair requires immutable frozen_inputs")
    forbidden = {"selected_level", "event_time_s", "message_delivery_schedule",
                 "reserve_identity", "reference_decision", "ground_truth"}
    if forbidden & set(frozen_inputs):
        raise ValueError("frozen_inputs contains evaluator-only condition fields")
    input_hash = canonical_sha256(dict(frozen_inputs))
    executable = axis["track"] == "protocol_components"
    private = {
        "factor_id": factor_id,
        "control_level": control_level,
        "variant_level": variant_level,
        "frozen_inputs_sha256": input_hash,
        "execution_scope": "protocol_components" if executable else "fixture_pending",
        "condition_recipe": {
            "control": _recipe(factor_id, control_level),
            "variant": _recipe(factor_id, variant_level),
        },
        "one_factor_at_a_time": True,
        "execution_validated": False,
    }
    public = {
        "schema": "eai.benchmark.factor_pair",
        "version": 1,
        "scene_key": scene_key,
        "factor_id": factor_id,
        "levels": [control_level, variant_level],
        "frozen_inputs_sha256": input_hash,
        "participant_visibility": "factor identity and compared levels only; condition schedule remains evaluator-only",
        "execution_scope": private["execution_scope"],
    }
    return {"public": public, "private": private}


def _recipe(factor_id: str, level: str) -> dict[str, Any]:
    if factor_id == "communication_quality":
        recipe = {"kind": "communication_contract_patch", "conditions": deepcopy(_COMMUNICATION_LEVELS[level])}
        if level == "stale":
            # TTL is a publication input, not a transport-contract field.
            # Keep it explicit so a protocol runner cannot mistake immediate
            # delivery for a stale-message trial.
            recipe["publication_validity_window_s"] = 0.5
        return recipe
    if factor_id == "team_continuity":
        return {"kind": "lifecycle_event", "event": level}
    return {"kind": "scene_fixture_level", "level": level,
            "requires": "scene-specific hidden domain adapter"}


def validate_factor_pair(pair: Mapping[str, Any], *, scene_key: str) -> dict[str, Any]:
    if not isinstance(pair, Mapping) or set(pair) != {"public", "private"}:
        raise ValueError("factor pair requires public and private records")
    public, private = pair["public"], pair["private"]
    if public.get("schema") != "eai.benchmark.factor_pair" or public.get("version") != 1:
        raise ValueError("unsupported factor pair public schema")
    if public.get("scene_key") != scene_key or private.get("factor_id") != public.get("factor_id"):
        raise ValueError("factor pair scene or factor mismatch")
    if public.get("levels") != [private.get("control_level"), private.get("variant_level")]:
        raise ValueError("factor pair level binding mismatch")
    _axis(scene_key, public["factor_id"])
    if public.get("frozen_inputs_sha256") != private.get("frozen_inputs_sha256"):
        raise ValueError("factor pair frozen-input binding mismatch")
    if private.get("one_factor_at_a_time") is not True:
        raise ValueError("factor pair must enforce one-factor comparison")
    if private.get("execution_validated") is not False:
        raise ValueError("factor pair cannot claim execution validation")
    return {"validated": True, "scene_key": scene_key,
            "factor_id": public["factor_id"], "execution_scope": private["execution_scope"]}


def run_communication_trial(level: str, *, messages: list[Mapping[str, Any]],
                            recipient: str = "participant", consumer_id: str = "task") -> dict[str, Any]:
    """Run one deterministic communication level and return replayable evidence.

    Each message mapping contains ``message_id``, ``producer_id``, ``estimate``
    and ``published_at_s``; ``valid_until_s`` is optional and is fixed by the
    trial level for the stale case.  The caller may consume only messages that
    are actually visible at the requested publication time.  This is a
    protocol evidence runner, so it does not advance any scheduler or claim a
    scene objective succeeded.
    """
    if level not in _COMMUNICATION_LEVELS:
        raise ValueError(f"unknown communication level: {level}")
    if not isinstance(messages, list) or not messages:
        raise ValueError("communication trial requires messages")
    contract = deepcopy(_COMMUNICATION_LEVELS[level])
    runtime = CommunicationRuntime(contract)
    validity = 0.5 if level == "stale" else 100.0
    for row in messages:
        if not isinstance(row, Mapping):
            raise ValueError("communication trial messages must be mappings")
        published = row.get("published_at_s", runtime.time_s)
        expiry = row.get("valid_until_s", float(published) + validity)
        runtime.publish(message_id=row["message_id"], producer_id=row["producer_id"],
                        recipients=[recipient], channel=row.get("channel", "observation"),
                        estimate=row["estimate"], published_at_s=published,
                        valid_until_s=expiry)
    horizon = max(float(row.get("advance_to_s", float(row.get("published_at_s", 0.0)) + 10.0))
                   for row in messages)
    runtime.advance(horizon)
    visible = runtime.public_messages(recipient=recipient)
    visible_ids = {row["message_id"] for row in visible}
    consumed = []
    for row in messages:
        if row.get("consume", False):
            if row["message_id"] not in visible_ids:
                continue
            consumed.append(runtime.consume(row["message_id"], recipient=recipient,
                                            consumer_id=consumer_id, time_s=horizon))
    report = runtime.report()
    replay = validate_communication_report(
        report, expected_initial_contract=contract)
    return {
        "schema": "eai.benchmark.communication_trial",
        "version": 1,
        "level": level,
        "observed_message_ids": sorted(row["message_id"] for row in visible),
        "consumed_message_ids": sorted(row["message_id"] for row in consumed),
        "communication_replay": replay,
        "execution_validated": False,
        "protocol_validated": bool(replay["validated"]),
        "report": report,
    }


def replay_protocol_factor_pair(pair: Mapping[str, Any], *, scene_key: str,
                                runner) -> dict[str, Any]:
    """Replay both sides of a protocol factor pair through an evaluator runner.

    The runner is deliberately injected by the evaluator.  It receives one
    private treatment recipe and must return only auditable protocol evidence;
    it must not mutate the public task or report evaluator truth.  A pair is
    useful for scoring only when each treatment was observed inside a real
    participant decision window and caused an observable availability or
    feasibility difference.  This function does not start Isaac and never
    upgrades ``execution_validated``.
    """
    validate_factor_pair(pair, scene_key=scene_key)
    private = pair["private"]
    if private.get("execution_scope") != "protocol_components":
        raise ValueError("protocol replay requires a protocol-components factor pair")
    if not callable(runner):
        raise ValueError("protocol replay requires an evaluator runner")

    trials = []
    for label in ("control", "variant"):
        recipe = deepcopy(private["condition_recipe"][label])
        try:
            evidence = runner(recipe)
        except Exception as exc:  # runner errors become an auditable failed trial
            evidence = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
        trials.append(_validate_protocol_trial(evidence, label=label))

    if not all(trial["status"] == "completed" for trial in trials):
        pair_status = "failed"
        factor_effect_validated = False
    else:
        paired_observable_difference = (
            trials[0]["observable_signature_after_sha256"]
            != trials[1]["observable_signature_after_sha256"])
        pair_status = "validated" if (
            all(trial["intervention_in_decision_window"]
                and trial["feasibility_observed"] for trial in trials)
            and paired_observable_difference
        ) else "inconclusive"
        factor_effect_validated = pair_status == "validated"
    return {
        "schema": "eai.benchmark.protocol_factor_pair_replay",
        "version": 1,
        "scene_key": scene_key,
        "factor_id": private["factor_id"],
        "levels": [private["control_level"], private["variant_level"]],
        "status": pair_status,
        "trials": trials,
        "paired_observable_difference": (
            factor_effect_validated
            or (all(trial["status"] == "completed" for trial in trials)
                and trials[0]["observable_signature_after_sha256"]
                != trials[1]["observable_signature_after_sha256"])),
        "factor_effect_validated": factor_effect_validated,
        "execution_validated": False,
        "physical_execution_validated": False,
        "remaining_scope": "protocol_components_only",
    }


def _validate_protocol_trial(value: Any, *, label: str) -> dict[str, Any]:
    """Validate runner evidence while retaining no hidden treatment data."""
    if not isinstance(value, Mapping):
        return {"label": label, "status": "error", "reason": "runner result must be an object"}
    required = {"status", "intervention_time_s", "decision_window",
                "observable_signature_before", "observable_signature_after",
                "feasibility_observed"}
    if not required <= set(value):
        return {"label": label, "status": "error",
                "reason": "runner result lacks decision-window evidence"}
    status = value["status"]
    if status != "completed":
        return {"label": label, "status": "error",
                "reason": "runner trial did not complete"}
    intervention_time = value["intervention_time_s"]
    if (isinstance(intervention_time, bool) or not isinstance(intervention_time, (int, float))
            or not math.isfinite(intervention_time)
            or intervention_time < 0):
        return {"label": label, "status": "error",
                "reason": "intervention_time_s must be finite and nonnegative"}
    window = value["decision_window"]
    opened = window.get("opened_at_s") if isinstance(window, Mapping) else None
    closed = window.get("closed_at_s") if isinstance(window, Mapping) else None
    if (isinstance(opened, bool) or not isinstance(opened, (int, float))
            or isinstance(closed, bool) or not isinstance(closed, (int, float))
            or not math.isfinite(opened) or not math.isfinite(closed)
            or opened < 0 or opened > closed):
        return {"label": label, "status": "error", "reason": "invalid decision window"}
    in_window = opened <= intervention_time <= closed
    before = value["observable_signature_before"]
    after = value["observable_signature_after"]
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return {"label": label, "status": "error",
                "reason": "observable signatures must be objects"}
    feasibility = value["feasibility_observed"]
    if not isinstance(feasibility, bool):
        return {"label": label, "status": "error",
                "reason": "feasibility_observed must be boolean"}
    try:
        before_hash = canonical_sha256(dict(before))
        after_hash = canonical_sha256(dict(after))
    except (TypeError, ValueError) as exc:
        return {"label": label, "status": "error",
                "reason": f"observable signatures must be finite JSON: {exc}"}
    return {
        "label": label,
        "status": "completed",
        "intervention_in_decision_window": in_window,
        "observable_change_within_trial": before_hash != after_hash,
        "observable_signature_before_sha256": before_hash,
        "observable_signature_after_sha256": after_hash,
        "feasibility_observed": feasibility,
    }


__all__ = ["compile_factor_pair", "replay_protocol_factor_pair",
           "run_communication_trial", "validate_factor_pair"]
