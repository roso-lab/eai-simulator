"""Versioned, fail-closed checkpoint and offline sham contracts.

This module is deliberately smaller than a simulator checkpoint.  It gives the
Goal track one stable serialization boundary for future native providers while
making it impossible for a symbolic fixture to claim protocol replay support.
The offline sham helper only checks that two branches start from the same
serialized snapshot and that event-keyed random draws are order independent.
It does not capture or restore PhysX, participant memory, communication queues,
or a simulation clock.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from EAI.keyed_random import KeyedRandom


CHECKPOINT_SCHEMA = "eai.benchmark.goal_checkpoint"
CHECKPOINT_VERSION = 1
SHAM_SCHEMA = "eai.benchmark.goal_offline_sham_fixture"
SHAM_VERSION = 1

STATE_DOMAINS = (
    "environment_physics",
    "participant_recoverable",
    "visible_history",
    "rng_and_clocks",
    "host_scheduler_and_pending_communication",
    "deployment_gate_epoch_and_assessment_history",
    "preregistered_remaining_task_oracle_and_cached_truth",
)
DOMAIN_STATUSES = frozenset({"verified", "missing"})
BRANCH_IDS = frozenset({"fact", "reference", "sham", "intervention"})


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("checkpoint values must be finite JSON values") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def build_checkpoint(
    *,
    checkpoint_id: str,
    task_id: str,
    task_contract_version: str,
    scene_version: str,
    code_version: str,
    asset_version: str,
    entity_keys: Sequence[str],
    condition_id: str,
    pair_repeat: str,
    trigger_event_key: str,
    state_domains: Mapping[str, str],
    state_payloads: Mapping[str, Any] | None = None,
    branch_id: str | None = None,
    parent_checkpoint_id: str | None = None,
    clock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an auditable checkpoint envelope without claiming restore support.

    ``state_domains`` is explicit and must cover every required domain.  A
    domain marked ``missing`` may have no payload.  Regardless of the supplied
    payloads, this constructor emits ``raw_only`` and unverified statuses;
    native providers must later issue a separate, independently checked receipt.
    """
    for value, name in (
        (checkpoint_id, "checkpoint_id"), (task_id, "task_id"),
        (task_contract_version, "task_contract_version"),
        (scene_version, "scene_version"), (code_version, "code_version"),
        (asset_version, "asset_version"), (condition_id, "condition_id"),
        (pair_repeat, "pair_repeat"), (trigger_event_key, "trigger_event_key"),
    ):
        _text(value, name)
    if branch_id is not None and branch_id not in BRANCH_IDS:
        raise ValueError(f"branch_id must be one of {sorted(BRANCH_IDS)}")
    if parent_checkpoint_id is not None:
        _text(parent_checkpoint_id, "parent_checkpoint_id")
    if not isinstance(entity_keys, Sequence) or isinstance(entity_keys, (str, bytes)):
        raise ValueError("entity_keys must be a sequence of strings")
    entities = sorted({_text(key, "entity_key") for key in entity_keys})
    if not entities:
        raise ValueError("entity_keys must not be empty")
    domains = dict(_mapping(state_domains, "state_domains"))
    unknown = set(domains) - set(STATE_DOMAINS)
    missing = set(STATE_DOMAINS) - set(domains)
    if unknown or missing:
        raise ValueError(f"state_domains must cover exactly required domains; unknown={sorted(unknown)}, missing={sorted(missing)}")
    invalid = {key: value for key, value in domains.items() if value not in DOMAIN_STATUSES}
    if invalid:
        raise ValueError(f"invalid state domain statuses: {invalid}")
    payloads = dict(state_payloads or {})
    extra_payloads = set(payloads) - set(STATE_DOMAINS)
    if extra_payloads:
        raise ValueError(f"state_payloads has unknown domains: {sorted(extra_payloads)}")
    for domain, status in domains.items():
        if status == "verified" and domain not in payloads:
            raise ValueError(f"verified domain {domain!r} requires a payload")
        if domain in payloads:
            _canonical(payloads[domain])
    if clock is None:
        clock_value = {"sim_time_s": None, "decision_step": None, "resolution_s": None}
    else:
        clock_value = dict(_mapping(clock, "clock"))
        for key in ("sim_time_s", "decision_step", "resolution_s"):
            if key in clock_value and clock_value[key] is not None:
                _finite_number(clock_value[key], f"clock.{key}")
        clock_value.setdefault("sim_time_s", None)
        clock_value.setdefault("decision_step", None)
        clock_value.setdefault("resolution_s", None)
    payload_hashes = {
        domain: _digest(payloads[domain]) for domain in sorted(payloads)
    }
    body = {
        "schema": CHECKPOINT_SCHEMA,
        "version": CHECKPOINT_VERSION,
        "checkpoint_id": checkpoint_id,
        "task_id": task_id,
        "task_contract_version": task_contract_version,
        "scene_version": scene_version,
        "code_version": code_version,
        "asset_version": asset_version,
        "entity_keys": entities,
        "condition_id": condition_id,
        "pair_repeat": pair_repeat,
        "trigger_event_key": trigger_event_key,
        "branch_id": branch_id,
        "parent_checkpoint_id": parent_checkpoint_id,
        "state_domains": {domain: domains[domain] for domain in STATE_DOMAINS},
        "state_payloads": deepcopy(payloads),
        "state_payload_sha256": payload_hashes,
        "clock": clock_value,
        "restore_status": "unverified",
        "replay_status": "stochastic_replay_unverified",
        "strict_branch_comparison_eligible": False,
        "raw_only": True,
        "not_protocol_score": True,
    }
    return {**body, "sha256": _digest(body)}


def validate_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Validate structure and content hashes, returning a defensive copy."""
    data = dict(_mapping(checkpoint, "checkpoint"))
    if data.get("schema") != CHECKPOINT_SCHEMA or data.get("version") != CHECKPOINT_VERSION:
        raise ValueError("unsupported checkpoint schema or version")
    supplied = data.get("sha256")
    if not isinstance(supplied, str) or supplied != _digest({key: value for key, value in data.items() if key != "sha256"}):
        raise ValueError("checkpoint digest mismatch")
    domains = data.get("state_domains")
    payloads = data.get("state_payloads")
    hashes = data.get("state_payload_sha256")
    _mapping(domains, "state_domains")
    _mapping(payloads, "state_payloads")
    _mapping(hashes, "state_payload_sha256")
    if set(domains) != set(STATE_DOMAINS) or any(value not in DOMAIN_STATUSES for value in domains.values()):
        raise ValueError("checkpoint state domains are incomplete or invalid")
    if set(payloads) != set(hashes):
        raise ValueError("checkpoint payload hash inventory mismatch")
    for domain, payload in payloads.items():
        if hashes.get(domain) != _digest(payload):
            raise ValueError(f"checkpoint payload digest mismatch for {domain}")
        if domains[domain] == "missing":
            raise ValueError(f"missing domain {domain} cannot carry a payload")
    if data.get("raw_only") is not True or data.get("strict_branch_comparison_eligible") is not False:
        raise ValueError("checkpoint must remain raw-only and ineligible")
    return deepcopy(data)


def restore_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Reject symbolic checkpoints until a native restore receipt exists."""
    validated = validate_checkpoint(checkpoint)
    raise RuntimeError(
        "checkpoint restore is unavailable without a native environment and participant provider; "
        f"checkpoint {validated['checkpoint_id']!r} remains unverified")


def build_offline_sham_fixture(
    checkpoint: Mapping[str, Any], *, seed: int, process: str,
    entity: str, event_keys: Sequence[str], branch_order: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic interface fixture for sham branch tests only."""
    base = validate_checkpoint(checkpoint)
    _text(process, "process")
    _text(entity, "entity")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    keys = [_text(key, "event_key") for key in event_keys]
    if not keys or len(set(keys)) != len(keys):
        raise ValueError("event_keys must be nonempty and unique")
    orders = dict(branch_order or {"fact": keys, "sham": list(reversed(keys))})
    if set(orders) != {"fact", "sham"}:
        raise ValueError("branch_order must contain fact and sham permutations of event_keys")
    for order in orders.values():
        if set(order) != set(keys) or len(order) != len(keys):
            raise ValueError("branch_order must contain fact and sham permutations of event_keys")
    rng = KeyedRandom(seed, process)
    branch_draws = {}
    for branch, order in orders.items():
        branch_draws[branch] = {
            key: float(rng.uniform(entity, key, 0.0, 1.0)) for key in order
        }
    body = {
        "schema": SHAM_SCHEMA,
        "version": SHAM_VERSION,
        "fixture_only": True,
        "raw_only": True,
        "not_protocol_score": True,
        "checkpoint_id": base["checkpoint_id"],
        "checkpoint_sha256": base["sha256"],
        "restore_status": "unverified_for_protocol_replay",
        "replay_status": "stochastic_replay_unverified",
        "strict_branch_comparison_eligible": False,
        "rng": {"scheme": "keyed", "seed": seed, "process": process, "entity": entity,
                "event_keys": keys, "branch_draws": branch_draws},
        "branches": {branch: {"state_sha256": base["sha256"], "event_order": list(order)}
                     for branch, order in orders.items()},
    }
    return {**body, "sha256": _digest(body)}


def validate_offline_sham_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(_mapping(fixture, "fixture"))
    if data.get("schema") != SHAM_SCHEMA or data.get("version") != SHAM_VERSION:
        raise ValueError("unsupported sham fixture schema or version")
    if data.get("fixture_only") is not True or data.get("raw_only") is not True:
        raise ValueError("sham fixture must remain offline and raw-only")
    supplied = data.get("sha256")
    if supplied != _digest({key: value for key, value in data.items() if key != "sha256"}):
        raise ValueError("sham fixture digest mismatch")
    branches = _mapping(data.get("branches"), "fixture.branches")
    rng = _mapping(data.get("rng"), "fixture.rng")
    draws = _mapping(rng.get("branch_draws"), "fixture.rng.branch_draws")
    if set(branches) != {"fact", "sham"} or set(draws) != {"fact", "sham"}:
        raise ValueError("sham fixture requires fact and sham branches")
    if dict(draws["fact"]) != dict(draws["sham"]):
        raise ValueError("keyed random draws differ between sham branch orderings")
    if any(branch.get("state_sha256") != data.get("checkpoint_sha256") for branch in branches.values()):
        raise ValueError("sham branches do not share the checkpoint snapshot")
    return deepcopy(data)


__all__ = [
    "BRANCH_IDS", "CHECKPOINT_SCHEMA", "CHECKPOINT_VERSION", "DOMAIN_STATUSES",
    "SHAM_SCHEMA", "SHAM_VERSION", "STATE_DOMAINS", "build_checkpoint",
    "validate_checkpoint", "restore_checkpoint", "build_offline_sham_fixture",
    "validate_offline_sham_fixture",
]
