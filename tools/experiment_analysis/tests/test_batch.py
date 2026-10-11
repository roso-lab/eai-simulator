from copy import deepcopy

import pytest

from tools.experiment_analysis.batch import assemble
from tools.experiment_analysis.engine import analyze
from tools.experiment_analysis.registration import freeze
from tools.experiment_analysis.schema import AnalysisError, digest
from tools.experiment_analysis.synthetic_batch import collect


@pytest.fixture(scope="module")
def collected(tmp_path_factory):
    return collect(tmp_path_factory.mktemp("batch") / "runs")


@pytest.fixture
def inputs(collected):
    return deepcopy(collected[1:])


def _audit(audit, base):
    return next(o for o in audit["observations"] if o["identity"]["unit_id"] == base and o["identity"]["repeat_id"] == "r0")


def _outcome(source, base):
    return next(o for o in source["outcomes"]["records"] if o["outcome"]["unit_id"] == base and o["outcome"]["repeat_id"] == "r0")


def _attempt(source, branch_id):
    return next(a for a in source["handoff"]["attempts"] if a["identity"]["branch_id"] == branch_id)


def _rehash(source, attempt):
    attempt["identity_digest"] = digest(attempt["identity"])
    attempt["episode_digest"] = digest(attempt["episode"]) if attempt["episode"] else None
    for outcome in source["outcomes"]["records"]:
        if outcome["branch_id"] == attempt["identity"]["branch_id"]:
            outcome["identity_digest"] = attempt["identity_digest"]


def test_two_sealed_batches_retry_and_method_failure_are_deterministic(inputs):
    design, registration, sources = inputs
    data, audit = assemble(design, registration, sources)
    assert len(data["records"]) == 12
    assert audit["status"] == "complete"
    assert _audit(audit, "c0/u0")["attempts"][0]["disposition"] == "allowed_infrastructure_retry"
    assert _audit(audit, "c0/u0")["attempts"][1]["disposition"] == "selected"
    failure = _audit(audit, "c0/u1")
    assert [a["disposition"] for a in failure["attempts"]] == ["selected", "after_terminal"]
    assert failure["attempts"][0]["method_outcome"] == "timeout"
    assert analyze(design, data)["results"][0]["estimate"] == pytest.approx(5 / 12)
    assert assemble(design, registration, list(reversed(sources))) == (data, audit)


@pytest.mark.parametrize("missing", ["source", "attempt", "episode", "evaluation"])
def test_missing_predecessor_cannot_be_replaced_by_later_success(inputs, missing):
    d, reg, sources = inputs
    first = sources[0]
    outcome = _outcome(first, "c0/u0")
    attempt = _attempt(first, outcome["branch_id"])
    if missing == "source":
        sources.pop(0)
    else:
        first["outcomes"]["records"].remove(outcome)
        if missing == "attempt":
            first["handoff"]["attempts"].remove(attempt)
        elif missing == "episode":
            attempt.update(episode=None, episode_digest=None, status="missing_terminal_episode")
    data, audit = assemble(d, reg, sources)
    row = _audit(audit, "c0/u0")
    assert row["selected"] is False
    assert row["attempts"][1]["disposition"] == "blocked_by_predecessor"
    assert audit["status"] == "incomplete"
    assert analyze(d, data)["results"][0]["estimate"] is None


def test_evaluator_cannot_relabel_method_failure_as_infrastructure(inputs):
    d, reg, sources = inputs
    raw = _outcome(sources[0], "c0/u1")["outcome"]
    raw.update(status="infrastructure_invalid", method_outcome="not_run", values={}, reason="synthetic_transport_failure")
    with pytest.raises(AnalysisError, match="sealed episode reason"):
        assemble(d, reg, sources)


def test_evaluator_cannot_promote_infrastructure_to_valid(inputs):
    d, reg, sources = inputs
    raw = _outcome(sources[0], "c0/u0")["outcome"]
    raw.update(status="valid", method_outcome="completed", values={"outcome": 1})
    del raw["reason"]
    with pytest.raises(AnalysisError, match="cannot supply a valid"):
        assemble(d, reg, sources)


def test_unallowlisted_infrastructure_is_terminal(inputs):
    d, reg, sources = inputs
    schedule = reg["schedule"]
    schedule["retry_policy"]["infrastructure_reasons"] = ["different_reason"]
    reg = freeze(d, schedule)
    for source in sources:
        source["outcomes"]["selection_policy_digest"] = reg["registration_digest"]
    data, audit = assemble(d, reg, sources)
    assert [a["disposition"] for a in _audit(audit, "c0/u0")["attempts"]] == ["selected", "after_terminal"]
    assert audit["status"] == "incomplete"
    assert analyze(d, data)["results"][0]["estimate"] is None


@pytest.mark.parametrize("field", ["task_version", "system_version", "source_revision", "checkpoint_digest", "task_contract_digest",
                                   "scene_build_digest", "asset_resolution_digest", "scene_build_order_digest", "skill_adapter_version",
                                   "rng_namespace", "communication_rng_namespace", "pair_group_id", "pair_seed", "slot", "metadata"])
def test_retry_cannot_change_execution_context_even_with_consistent_hashes(inputs, field):
    d, reg, sources = inputs
    source = sources[1]
    attempt = _attempt(source, _outcome(source, "c0/u0")["branch_id"])
    attempt["identity"][field] = 19 if field in {"pair_seed", "slot"} else {"checkpoint_round": "e0", "changed": True} if field == "metadata" else "different"
    if field != "metadata":
        attempt["episode"][field] = attempt["identity"][field]
    _rehash(source, attempt)
    with pytest.raises(AnalysisError, match="frozen context"):
        assemble(d, reg, sources)


@pytest.mark.parametrize("attack", ["scope", "plan", "evaluator", "policy", "duplicate_run", "duplicate_seal", "unknown_run"])
def test_sources_cannot_silently_mix(inputs, attack):
    d, reg, sources = inputs
    if attack == "scope":
        source = sources[1]
        source["handoff"].update(execution_scope="measured", provenance="measured_unverified")
        for attempt in source["handoff"]["attempts"]:
            attempt["identity"]["execution_scope"] = attempt["episode"]["execution_scope"] = "measured"
            _rehash(source, attempt)
    elif attack == "plan":
        sources[1]["handoff"]["plan_digest"] = digest("other_plan")
    elif attack == "evaluator":
        sources[1]["outcomes"]["evaluator"] = dict(reg["schedule"]["evaluator"], version="other")
    elif attack == "policy":
        sources[1]["outcomes"]["selection_policy_digest"] = digest("other_policy")
    elif attack == "duplicate_run":
        sources.append(deepcopy(sources[0]))
    elif attack == "duplicate_seal":
        sources[1]["handoff"]["seal_sha256"] = sources[0]["handoff"]["seal_sha256"]
    else:
        sources[1]["handoff"]["run_id"] = "unknown"
    with pytest.raises(AnalysisError):
        assemble(d, reg, sources)


@pytest.mark.parametrize("attack", ["omit_observation", "duplicate_observation", "reuse_attempt", "truncate_first", "ordinal", "bad_base"])
def test_freeze_rejects_selective_or_ambiguous_schedules(inputs, attack):
    d, reg, _ = inputs
    schedule = reg["schedule"]
    entry = schedule["observations"][0]
    if attack == "omit_observation":
        schedule["observations"].pop()
    elif attack == "duplicate_observation":
        schedule["observations"].append(deepcopy(entry))
    elif attack == "reuse_attempt":
        schedule["observations"][1]["attempts"] = deepcopy(entry["attempts"])
    elif attack == "truncate_first":
        entry["attempts"][0].update(attempt=1, branch_id=entry["attempts"][0]["branch_id"].replace("#attempt-0", "#attempt-1"))
    elif attack == "ordinal":
        entry["attempts"][0]["branch_id"] += "-1"
    else:
        entry["identity"]["base_instance_id"] = "other"
    with pytest.raises(AnalysisError):
        freeze(d, schedule)


def test_unregistered_observed_retry_cannot_be_hidden(inputs):
    d, reg, sources = inputs
    source = sources[1]
    attempt = deepcopy(source["handoff"]["attempts"][0])
    attempt["identity"]["branch_id"] = attempt["episode"]["branch_id"] = "hidden#attempt-0"
    _rehash(source, attempt)
    source["handoff"]["attempts"].append(attempt)
    with pytest.raises(AnalysisError, match="unregistered attempt"):
        assemble(d, reg, sources)


def test_registration_tampering_is_not_an_implicit_refreeze(inputs):
    d, reg, sources = inputs
    reg["schedule"]["retry_policy"]["infrastructure_reasons"].append("another")
    with pytest.raises(AnalysisError, match="registration content changed"):
        assemble(d, reg, sources)


def test_valid_first_attempt_schedule(inputs):
    d, reg, sources = inputs
    schedule = reg["schedule"]
    schedule["retry_policy"] = {"mode": "first_attempt", "infrastructure_reasons": []}
    for entry in schedule["observations"]:
        entry["attempts"] = entry["attempts"][:1]
    reg = freeze(d, schedule)
    refs = {(a["run_id"], a["branch_id"]) for e in schedule["observations"] for a in e["attempts"]}
    for source in sources:
        run = source["handoff"]["run_id"]
        source["handoff"]["attempts"] = [a for a in source["handoff"]["attempts"] if (run, a["identity"]["branch_id"]) in refs]
        source["outcomes"]["records"] = [r for r in source["outcomes"]["records"] if (run, r["branch_id"]) in refs]
        source["outcomes"]["selection_policy_digest"] = reg["registration_digest"]
    _, audit = assemble(d, reg, sources)
    assert _audit(audit, "c0/u0")["attempts"][0]["disposition"] == "selected"
    assert audit["status"] == "incomplete"
