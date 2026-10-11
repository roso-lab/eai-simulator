"""Content-frozen collection schedule; never a trusted preregistration service."""

from . import ANALYSIS_VERSION
from .planning import expected_observations, readiness
from .schema import AnalysisError, Design, digest, integer, obj, record_key, sha, strings, text


IDENTITY = "unit_id base_instance_id member system_id condition_id branch_role checkpoint_round repeat_id"
EXECUTION_FIELDS = ("scene_key task_id task_version system_version source_revision checkpoint_digest "
                    "restore_checkpoint_digest task_contract_digest scene_build_digest asset_resolution_digest "
                    "scene_build_order_digest skill_adapter_version rng_namespace communication_rng_namespace intervention_point pair_group_id metadata_digest")


def execution_identity(identity: dict) -> dict:
    """The frozen, repeat-specific context that must remain constant on retry."""
    context = {name: identity.get(name) for name in EXECUTION_FIELDS.split()}
    if "metadata" in identity:
        if not isinstance(identity["metadata"], dict):
            raise AnalysisError("execution metadata must be an object")
        context["metadata_digest"] = digest(identity["metadata"])
        mode = identity["metadata"].get("checkpoint_receipt_mode", "restore")
        if mode != "restore":
            from tools.benchmark_branch.initialization import validate_initialization_job
            from tools.benchmark_branch.models import BranchJob
            validate_initialization_job(BranchJob.from_mapping(identity), session_backend=True)
            context["checkpoint_receipt_mode"] = mode
            context["initialization_spec_digest"] = identity["checkpoint_digest"]
    else:
        for name in ("checkpoint_receipt_mode", "initialization_spec_digest"):
            if name in identity:
                context[name] = identity[name]
    fresh = context.get("checkpoint_receipt_mode") == "fresh_initialization_v1"
    if "checkpoint_receipt_mode" in context or "initialization_spec_digest" in context:
        if not fresh or context.get("initialization_spec_digest") != context["checkpoint_digest"]:
            raise AnalysisError("invalid frozen fresh initialization identity")
        if context["restore_checkpoint_digest"] != "":
            raise AnalysisError("fresh initialization cannot identify a restored checkpoint")
    for name, value in context.items():
        if not (value == "" and (name == "intervention_point" or name == "restore_checkpoint_digest" and fresh)):
            text(value)
    context["pair_seed"] = integer(identity.get("pair_seed"))
    context["slot"] = integer(identity.get("slot"))
    sha(context["metadata_digest"])
    return context


def validate_schedule(design_data: dict, schedule: dict) -> None:
    design = Design.parse(design_data)
    obj(schedule, "schema design_digest evaluator execution_scope retry_policy batches observations")
    if schedule["schema"] != ANALYSIS_VERSION + ".collection_schedule" or schedule["design_digest"] != design.digest:
        raise AnalysisError("schedule schema/design mismatch")
    if readiness(design_data)["status"] != "ready_for_collection":
        raise AnalysisError("cannot freeze a structurally insufficient design")
    evaluator = obj(schedule["evaluator"], "id version source_digest")
    text(evaluator["id"])
    text(evaluator["version"])
    sha(evaluator["source_digest"])
    if schedule["execution_scope"] not in {"protocol_synthetic", "measured", "measured_diagnostic"}:
        raise AnalysisError("unsupported registered execution scope")
    policy = obj(schedule["retry_policy"], "mode infrastructure_reasons")
    reasons = strings(policy["infrastructure_reasons"], empty=True)
    if policy["mode"] not in {"first_attempt", "infrastructure_retry"}:
        raise AnalysisError("unsupported attempt-selection mode")
    if (policy["mode"] == "first_attempt" and reasons) or (policy["mode"] == "infrastructure_retry" and not reasons):
        raise AnalysisError("retry policy reasons contradict its mode")
    # No outcome-based policy, callable, import or executable string is accepted.
    if not isinstance(schedule["batches"], list) or not schedule["batches"]:
        raise AnalysisError("schedule requires a batch array")
    batches = {}
    for batch in schedule["batches"]:
        obj(batch, "run_id plan_digest")
        run = text(batch["run_id"])
        if run in batches:
            raise AnalysisError("duplicate scheduled run_id")
        batches[run] = sha(batch["plan_digest"])
    units = {u.id: u for u in design.units}
    expected, seen, refs, used_runs = expected_observations(design), set(), set(), set()
    if not isinstance(schedule["observations"], list):
        raise AnalysisError("observations must be an array")
    for entry in schedule["observations"]:
        obj(entry, "identity execution_identity attempts")
        obj(entry["execution_identity"], EXECUTION_FIELDS + " pair_seed slot",
            "checkpoint_receipt_mode initialization_spec_digest")
        execution_identity(entry["execution_identity"])
        if (entry["execution_identity"]["restore_checkpoint_digest"] == ""
                and schedule["execution_scope"] != "measured_diagnostic"):
            raise AnalysisError("only fresh measured diagnostics may omit a restore digest")
        ident = obj(entry["identity"], IDENTITY)
        for value in ident.values():
            text(value)
        key = record_key(ident)
        if key not in expected or key in seen:
            raise AnalysisError("duplicate or unplanned scheduled observation")
        seen.add(key)
        if dict(units[ident["unit_id"]].members)[ident["member"]] != ident["base_instance_id"]:
            raise AnalysisError("scheduled base identity mismatch")
        attempts = entry["attempts"]
        if not isinstance(attempts, list) or not attempts:
            raise AnalysisError("each observation needs an ordered attempt array")
        if policy["mode"] == "first_attempt" and len(attempts) != 1:
            raise AnalysisError("first_attempt policy permits exactly one scheduled attempt")
        previous = {}
        for attempt in attempts:
            obj(attempt, "run_id branch_id attempt")
            run, branch = text(attempt["run_id"]), text(attempt["branch_id"])
            ordinal = integer(attempt["attempt"])
            if run not in batches or (run, branch) in refs:
                raise AnalysisError("unknown run or reused scheduled branch attempt")
            logical = branch.rsplit("#attempt-", 1)[0]
            if ordinal != previous.get(run, (-1, logical))[0] + 1 or logical != previous.get(run, (-1, logical))[1]:
                raise AnalysisError("attempts within a run must start at zero and be contiguous for one logical branch")
            if branch.endswith(f"#attempt-{ordinal}") is False:
                raise AnalysisError("branch_id must carry its exact attempt suffix")
            previous[run] = (ordinal, logical)
            refs.add((run, branch))
            used_runs.add(run)
    if seen != set(expected) or used_runs != set(batches):
        raise AnalysisError("schedule must cover every expected observation and use every batch")


def freeze(design_data: dict, schedule: dict) -> dict:
    """Return an immutable-content envelope; caller must persist it before runs."""
    validate_schedule(design_data, schedule)
    # Detach nested caller mappings; the digest always covers the exact payload.
    import json
    payload = json.loads(json.dumps(schedule, allow_nan=False))
    return {"schema": ANALYSIS_VERSION + ".registration", "schedule": payload,
            "registration_digest": digest(payload), "publishable": False,
            "qualification": "content_frozen_registration_time_unverified"}


def load_registration(design_data: dict, registration: dict) -> dict:
    obj(registration, "schema schedule registration_digest publishable qualification")
    if registration["schema"] != ANALYSIS_VERSION + ".registration" or registration["publishable"] is not False:
        raise AnalysisError("registration schema/qualification mismatch")
    if registration["qualification"] != "content_frozen_registration_time_unverified":
        raise AnalysisError("registration cannot self-certify chronology")
    if sha(registration["registration_digest"]) != digest(registration["schedule"]):
        raise AnalysisError("registration content changed")
    validate_schedule(design_data, registration["schedule"])
    return registration["schedule"]
