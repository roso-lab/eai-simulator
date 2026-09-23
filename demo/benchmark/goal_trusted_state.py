"""Private state grounding for the registered remaining-task oracle.

Hashes bind records; they do not authenticate measurements. Source readers
are evaluator-owned callables, installed by the host, never participant data.
This layer cannot produce a feasibility label. Only the registered transition
solver may do so, with its separate model-completeness certificate.
"""

from collections.abc import Mapping
from copy import deepcopy
import math

from .goal_remaining_feasibility_oracle import RegisteredRemainingTaskOracle
from .provenance import canonical_sha256

SCENES = ("factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant")
EVIDENCE_KINDS = {"physical_completion", "business_receipt", "continuous_service",
                  "observation_record"}
_RELEASE = {"execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False}


def _names(values, field, *, nonempty=False):
    if (not isinstance(values, list) or (nonempty and not values)
            or any(not isinstance(v, str) or not v.strip() for v in values)
            or len(set(values)) != len(values)):
        raise ValueError(f"{field} must contain unique nonempty names")
    return set(values)


def _digest(record, field):
    return canonical_sha256({key: value for key, value in record.items() if key != field})


def _sealed(record, field):
    if not isinstance(record, Mapping) or record.get(field) != _digest(record, field):
        raise ValueError(f"{field} mismatch")


def freeze_state_contract(public_task, registration, *, predicate_bindings,
                          outcome_predicates, source_versions, entity_ids,
                          asset_manifest_sha256, skill_contract_sha256):
    """Freeze all fact sources and outcome bindings before an episode starts.

    Each fact, including false/intermediate facts, needs its own measurement.
    An outcome can require multiple predicates; a completed physical predicate
    remains separate from its outstanding receipt. Only applicable evidence
    kinds are required: this does not add a continuous-service requirement to
    a task which has none. No assignment or witness enters this contract.
    """
    contract = {
        "schema": "eai.benchmark.trusted_state_contract", "version": 1,
        "visibility": "evaluator_only", "scene_key": public_task["mission"]["scene_key"],
        "task_sha256": canonical_sha256(public_task),
        "registration_sha256": registration["registration_sha256"],
        "outcome_ids": [row["id"] for row in public_task["required_outcomes"]],
        "predicate_bindings": deepcopy(predicate_bindings),
        "outcome_predicates": deepcopy(outcome_predicates),
        "source_versions": deepcopy(source_versions), "entity_ids": list(entity_ids),
        "asset_manifest_sha256": asset_manifest_sha256,
        "skill_contract_sha256": skill_contract_sha256,
        **_RELEASE,
    }
    contract["contract_sha256"] = _digest(contract, "contract_sha256")
    validate_state_contract(contract, registration)
    return contract


def validate_state_contract(contract, registration):
    _sealed(contract, "contract_sha256")
    _sealed(registration, "registration_sha256")
    if (contract.get("schema") != "eai.benchmark.trusted_state_contract"
            or type(contract.get("version")) is not int or contract["version"] != 1
            or contract.get("scene_key") not in SCENES
            or contract.get("visibility") != "evaluator_only"):
        raise ValueError("invalid trusted state contract identity")
    if (contract["registration_sha256"] != registration["registration_sha256"]
            or contract["task_sha256"] != registration["task_sha256"]):
        raise ValueError("state contract registration/task mismatch")
    for name in ("asset_manifest_sha256", "skill_contract_sha256"):
        value = contract.get(name)
        if (not isinstance(value, str) or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)):
            raise ValueError(f"{name} requires a SHA256 identity")
    if any(contract.get(key) is not False for key in _RELEASE):
        raise ValueError("state contract cannot promote release flags")
    entities = _names(contract["entity_ids"], "entity_ids", nonempty=True)
    sources = contract["source_versions"]
    if (not isinstance(sources, Mapping) or not sources
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                   for k, v in sources.items())):
        raise ValueError("source_versions requires named versioned readers")
    facts = set(registration["model"]["fact_ids"])
    bindings = contract["predicate_bindings"]
    if not isinstance(bindings, Mapping) or set(bindings) != facts:
        raise ValueError("every model fact requires a source binding")
    for binding in bindings.values():
        if (binding["evidence_kind"] not in EVIDENCE_KINDS
                or binding["source_id"] not in sources
                or not _names(binding["entity_ids"], "predicate entity_ids", nonempty=True) <= entities):
            raise ValueError("invalid predicate source/entity binding")
    if {b["source_id"] for b in bindings.values()} != set(sources):
        raise ValueError("unused source in state contract")
    outcomes = _names(contract["outcome_ids"], "outcome_ids", nonempty=True)
    mapping = contract["outcome_predicates"]
    if not isinstance(mapping, Mapping) or set(mapping) != outcomes:
        raise ValueError("every public outcome requires terminal predicate bindings")
    covered = set()
    for values in mapping.values():
        covered |= _names(values, "outcome predicates", nonempty=True)
    if covered != set(registration["model"]["terminal_all"]):
        raise ValueError("outcome predicates must cover exactly the model hard terminal")


def ground_state(contract, registration, *, measurement_id, state, readbacks):
    """Validate trusted source readbacks, returning grounding, never truth.

    ``readbacks`` must come from host-installed source readers. Explicit false
    measurements differ from missing measurements. Host time and all dynamic
    versions are checked independently of the readback's content hash.
    """
    result = {"status": "failed", "evidence_refs": [], **_RELEASE}
    try:
        validate_state_contract(contract, registration)
        point = next((p for p in registration["measurement_points"]
                      if p["id"] == measurement_id), None)
        if point is None:
            raise ValueError("unregistered measurement point")
        if (type(state["time_s"]) not in (int, float)
                or not math.isfinite(state["time_s"]) or state["time_s"] < 0):
            raise ValueError("invalid trusted clock")
        versions = state["entity_versions"]
        if (not isinstance(versions, Mapping) or set(versions) != set(contract["entity_ids"])
                or any(type(v) is not int or v < 0 for v in versions.values())):
            raise ValueError("entity version binding mismatch")
        facts = _names(state["true_facts"], "true_facts")
        if not facts <= set(contract["predicate_bindings"]):
            raise ValueError("unknown state predicate")
        if (not set(point["required_true"]) <= facts or set(point["required_false"]) & facts):
            raise ValueError("state outside preregistered domain")
        if not isinstance(readbacks, Mapping) or set(readbacks) != set(contract["source_versions"]):
            raise ValueError("missing or unexpected source readback")
        refs = []
        state_hash = canonical_sha256(state)
        for source, version in contract["source_versions"].items():
            packet = readbacks[source]
            expected_facts = {key for key, b in contract["predicate_bindings"].items()
                              if b["source_id"] == source}
            expected_entities = {e for key in expected_facts
                                 for e in contract["predicate_bindings"][key]["entity_ids"]}
            expected = {
                "source_id": source, "source_version": version,
                "measurement_id": measurement_id, "state_sha256": state_hash,
                "measured_at_s": state["time_s"],
                "entity_versions": {e: versions[e] for e in expected_entities},
            }
            if any(packet.get(key) != value for key, value in expected.items()):
                raise ValueError(f"source binding mismatch: {source}")
            values = packet.get("predicates")
            if (not isinstance(values, Mapping) or set(values) != expected_facts
                    or any(type(v) is not bool for v in values.values())):
                raise ValueError(f"incomplete predicate measurements: {source}")
            if any(value != (key in facts) for key, value in values.items()):
                raise ValueError(f"predicate measurement disagrees with state: {source}")
            _names(packet.get("evidence_refs"), "evidence_refs", nonempty=True)
            refs.extend(packet["evidence_refs"])
        result.update(status="verified", state_sha256=state_hash,
                      evidence_refs=sorted(set(refs)),
                      state_contract_sha256=contract["contract_sha256"],
                      readbacks_sha256=canonical_sha256(readbacks))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        result["quality_failure"] = str(exc)
    return result


class GroundedRemainingTaskOracle(RegisteredRemainingTaskOracle):
    """Runner-compatible oracle with preregistered source readers and archive.

    The state provider returns ``measurement_id`` and the bounded solver state.
    Each reader receives the same pre-decision snapshot and state observation,
    and must independently measure its bound predicates. Supplying a Boolean
    label or a self-reported success flag is not a reader implementation.
    """

    def __init__(self, registration, contract, state_provider, source_readers):
        validate_state_contract(contract, registration)
        if (set(source_readers) != set(contract["source_versions"])
                or any(not callable(reader) for reader in source_readers.values())):
            raise ValueError("every registered source requires a trusted reader")
        self._state_contract = deepcopy(contract)
        self._measurements = []
        readers = dict(source_readers)

        def provide(snapshot):
            observation = deepcopy(state_provider(deepcopy(snapshot)))
            packets = {}
            try:
                for source, reader in readers.items():
                    packets[source] = reader(deepcopy(snapshot), deepcopy(observation))
                grounding = ground_state(
                    self._state_contract, self._registration,
                    measurement_id=observation["measurement_id"],
                    state=observation["state"], readbacks=packets)
            except Exception as exc:
                grounding = {"status": "failed", "evidence_refs": [],
                             "quality_failure": f"source_reader_error:{type(exc).__name__}:{exc}"}
            observation["grounding"] = grounding
            self._measurements.append({"assessment_id": snapshot["assessment_id"],
                                      "assessment_epoch": snapshot["assessment_epoch"],
                                      **deepcopy(observation), "readbacks": deepcopy(packets)})
            return observation

        super().__init__(registration, provide)

    def report(self):
        result = super().report()
        result["state_contract"] = deepcopy(self._state_contract)
        result["state_measurements"] = deepcopy(self._measurements)
        return result
