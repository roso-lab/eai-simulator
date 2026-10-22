from __future__ import annotations

import json
from pathlib import Path
import pytest

from benchmark.diagnostic_evidence_v3 import DIAGNOSTIC_SCHEMA, DiagnosticEvidenceError, DiagnosticEvidenceV3, read_diagnostic_evidence_v3
from benchmark.evidence import BranchLedger
from benchmark.protocol import BranchRole, RunKey
from benchmark.records_v3 import canonical_digest


def _payload(**overrides):
    value = {
        "schema": DIAGNOSTIC_SCHEMA, "evidence_scope": "diagnostic_only", "rankable": False,
        "experiment_identity": {"run_id": "tool-run", "repeat_id": "r1", "slot_generation": 2},
        "reference_paths": ["/tmp/reference.json"], "candidate_path": "/tmp/candidate.json",
        "process_failure_scope": "shared_session", "shutdown_status": "pending_timeout",
        "missing_branch_ids": [], "extra_branch_ids": [], "structural_mismatches": {},
        "field_max_errors": {"observation": 1.2e-7}, "max_numeric_error": 1.2e-7,
        "tolerance": 1e-5, "numeric_equivalence": True, "exact_equivalence": False,
        "qualification_flags": {name: False for name in ("formal_measured_qualified", "native_full_state_qualified", "process_isolation_qualified")},
        "qualification_status": "diagnostic_only",
    }
    value.update(overrides)
    value["experiment_identity_digest"] = canonical_digest(value["experiment_identity"])
    value["evidence_digest"] = canonical_digest(value)
    return value


def _bound_identity(role="fact"):
    return {
        "base_instance_id": "base", "system_id": "system", "condition_id": "condition",
        "repeat_id": "repeat", "checkpoint_round": "checkpoint", "branch_role": role,
        "untreated_history_id": "",
    }


def test_import_is_fail_closed_and_non_rankable():
    evidence = DiagnosticEvidenceV3.from_tool_mapping(_payload())
    assert evidence.rankable is False
    assert evidence.numeric_equivalence is True and evidence.exact_equivalence is False
    assert all(value is False for value in evidence.to_dict()["qualification_flags"].values())


def test_positive_qualification_or_rankable_flag_is_rejected():
    with pytest.raises(DiagnosticEvidenceError, match="rankable"):
        DiagnosticEvidenceV3.from_tool_mapping(_payload(rankable=True))
    flags = {name: False for name in ("formal_measured_qualified", "native_full_state_qualified", "process_isolation_qualified")}
    flags["native_full_state_qualified"] = True
    with pytest.raises(DiagnosticEvidenceError, match="positive qualification"):
        DiagnosticEvidenceV3.from_tool_mapping(_payload(qualification_flags=flags))


def test_round_trip_and_project_ledger_binding(tmp_path):
    reference = tmp_path / "reference.json"
    candidate = tmp_path / "candidate.json"
    reference.write_text("reference\n", encoding="utf-8")
    candidate.write_text("candidate\n", encoding="utf-8")
    payload = _payload(
        experiment_identity=_bound_identity(),
        reference_paths=[str(reference)], candidate_path=str(candidate),
    )
    payload["evidence_digest"] = canonical_digest({
        key: value for key, value in payload.items()
        if key not in {"provenance", "provenance_digest", "evidence_digest"}
    })
    evidence = DiagnosticEvidenceV3.from_tool_mapping(payload)
    path = evidence.write_json(tmp_path / "diagnostic.json")
    loaded = read_diagnostic_evidence_v3(path, artifact_roots=(tmp_path,))
    assert loaded.digest() == evidence.digest()
    ledger = BranchLedger(RunKey("base", "system", "condition", "repeat", "checkpoint", BranchRole.FACT))
    event = loaded.append_to_ledger(
        ledger,
        tick=0,
        ledger_identity={**ledger.run_key.__dict__, "branch_role": ledger.run_key.branch_role.value},
    )
    assert event.kind == "diagnostic_trace_comparison"
    assert event.payload["rankable"] is False


def test_ledger_binding_rejects_wrong_branch_identity():
    evidence = DiagnosticEvidenceV3.from_tool_mapping(_payload(experiment_identity=_bound_identity()))
    ledger = BranchLedger(RunKey("base", "system", "condition", "repeat", "checkpoint", BranchRole.FACT))
    identity = {**ledger.run_key.__dict__, "branch_role": ledger.run_key.branch_role.value}
    identity["branch_role"] = "reference"
    with pytest.raises(DiagnosticEvidenceError, match="does not match"):
        evidence.append_to_ledger(ledger, tick=0, ledger_identity=identity)


def test_digest_tampering_and_wrong_scope_are_rejected(tmp_path):
    evidence = DiagnosticEvidenceV3.from_tool_mapping(_payload())
    path = evidence.write_json(tmp_path / "diagnostic.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["exact_equivalence"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DiagnosticEvidenceError, match="digest mismatch"):
        read_diagnostic_evidence_v3(path)
    with pytest.raises(DiagnosticEvidenceError, match="process_failure_scope"):
        DiagnosticEvidenceV3.from_tool_mapping(_payload(process_failure_scope="independent_process"))


def test_provenance_is_preserved_and_core_digest_remains_compatible():
    provenance = {
        "command": "smoke", "cwd": "/tmp", "interpreter": "python",
        "exit_code": 124, "output_path": "trace.json", "output_digest": "sha256:x",
        "shutdown_status": "pending_timeout",
    }
    payload = _payload(provenance=provenance, provenance_digest=canonical_digest(provenance))
    payload["evidence_digest"] = canonical_digest({key: value for key, value in payload.items()
                                                    if key not in {"provenance", "provenance_digest", "evidence_digest"}})
    evidence = DiagnosticEvidenceV3.from_tool_mapping(payload)
    assert evidence.to_dict()["provenance"] == provenance
    path_payload = evidence.to_dict()
    assert path_payload["provenance_digest"] == canonical_digest(provenance)


def test_from_tool_mapping_rejects_tampered_digest_before_ledger_append():
    payload = _payload(experiment_identity=_bound_identity())
    payload["evidence_digest"] = "0" * 64
    with pytest.raises(DiagnosticEvidenceError, match="digest mismatch"):
        DiagnosticEvidenceV3.from_tool_mapping(payload)


def test_envelope_identity_mismatch_is_rejected_even_with_matching_external_identity():
    evidence = DiagnosticEvidenceV3.from_tool_mapping(
        _payload(experiment_identity=_bound_identity("reference"))
    )
    ledger = BranchLedger(RunKey("base", "system", "condition", "repeat", "checkpoint", BranchRole.FACT))
    identity = {**ledger.run_key.__dict__, "branch_role": ledger.run_key.branch_role.value}
    with pytest.raises(DiagnosticEvidenceError, match="identity field branch_role"):
        evidence.append_to_ledger(ledger, tick=0, ledger_identity=identity)


def test_envelope_identity_requires_all_run_key_fields():
    evidence = DiagnosticEvidenceV3.from_tool_mapping(_payload())
    ledger = BranchLedger(RunKey("base", "system", "condition", "repeat", "checkpoint", BranchRole.FACT))
    identity = {**ledger.run_key.__dict__, "branch_role": ledger.run_key.branch_role.value}
    with pytest.raises(DiagnosticEvidenceError, match="lacks RunKey fields"):
        evidence.append_to_ledger(ledger, tick=0, ledger_identity=identity)


def test_direct_constructor_cannot_append_or_mint_a_verified_envelope():
    evidence = DiagnosticEvidenceV3(
        experiment_identity=_bound_identity(), reference_paths=("r",), candidate_path="c",
        shutdown_status="pending_timeout", missing_branch_ids=(), extra_branch_ids=(),
        structural_mismatches={}, field_max_errors={}, max_numeric_error=0,
        tolerance=1, numeric_equivalence=True, exact_equivalence=False,
    )
    ledger = BranchLedger(RunKey("base", "system", "condition", "repeat", "checkpoint", BranchRole.FACT))
    identity = {**ledger.run_key.__dict__, "branch_role": ledger.run_key.branch_role.value}
    with pytest.raises(DiagnosticEvidenceError, match="verified immutable ingress"):
        evidence.append_to_ledger(ledger, tick=0, ledger_identity=identity)
    with pytest.raises(DiagnosticEvidenceError, match="verified immutable ingress"):
        evidence.write_json("/tmp/unverified-diagnostic-evidence.json")


def test_verified_identity_is_deeply_immutable():
    evidence = DiagnosticEvidenceV3.from_tool_mapping(_payload(experiment_identity={
        **_bound_identity("reference"), "nested": {"items": ["original"]},
    }))
    with pytest.raises(TypeError):
        evidence.experiment_identity["branch_role"] = "fact"
    with pytest.raises(TypeError):
        evidence.experiment_identity["nested"]["items"][0] = "changed"


def test_conflicting_role_aliases_are_rejected_at_ledger_binding():
    evidence = DiagnosticEvidenceV3.from_tool_mapping(_payload(experiment_identity={
        **_bound_identity("fact"), "branch_type": "reference",
    }))
    ledger = BranchLedger(RunKey("base", "system", "condition", "repeat", "checkpoint", BranchRole.FACT))
    identity = {**ledger.run_key.__dict__, "branch_role": ledger.run_key.branch_role.value}
    with pytest.raises(DiagnosticEvidenceError, match="conflicting"):
        evidence.append_to_ledger(ledger, tick=0, ledger_identity=identity)


def test_missing_history_cannot_bind_to_nonempty_history_ledger():
    identity_payload = _bound_identity()
    identity_payload.pop("untreated_history_id")
    evidence = DiagnosticEvidenceV3.from_tool_mapping(_payload(experiment_identity=identity_payload))
    ledger = BranchLedger(RunKey(
        "base", "system", "condition", "repeat", "checkpoint", BranchRole.FACT, "history-1"
    ))
    identity = {**ledger.run_key.__dict__, "branch_role": ledger.run_key.branch_role.value}
    with pytest.raises(DiagnosticEvidenceError, match="lacks untreated_history_id"):
        evidence.append_to_ledger(ledger, tick=0, ledger_identity=identity)


def test_tool_canonical_non_ascii_and_integer_numeric_payload_is_accepted():
    import hashlib

    def tool_digest(value):
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    payload = _payload(
        experiment_identity={**_bound_identity(), "base_instance_id": "实例"},
        max_numeric_error=0, tolerance=1,
    )
    payload["experiment_identity_digest"] = tool_digest(payload["experiment_identity"])
    payload["evidence_digest"] = tool_digest({
        key: value for key, value in payload.items()
        if key not in {"provenance", "provenance_digest", "evidence_digest"}
    })
    evidence = DiagnosticEvidenceV3.from_tool_mapping(payload)
    assert evidence.max_numeric_error == 0
    assert evidence.tolerance == 1
    assert evidence.experiment_identity["base_instance_id"] == "实例"


def test_fixed_cross_repository_golden_fixtures_round_trip_from_files():
    fixture_root = Path(__file__).parents[1] / "fixtures"
    artifact_root = Path("/home/pupil/Documents/Codex/agent-team/workspaces/heterocoord-tool-qualification-round2-20261020/artifacts")
    tool_root = Path("/home/pupil/eai-simulator-worktrees/semantic-map-tool-release/tools/benchmark_branch/fixtures")
    for name in ("golden_diagnostic_envelope.json", "golden_diagnostic_nonascii_int.json"):
        project_fixture = fixture_root / name
        tool_fixture = tool_root / name
        assert project_fixture.is_file() and tool_fixture.is_file()
        assert project_fixture.read_bytes() == tool_fixture.read_bytes()
        evidence = read_diagnostic_evidence_v3(project_fixture, artifact_roots=(artifact_root,))
        payload = json.loads(project_fixture.read_text(encoding="utf-8"))
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
        assert evidence.digest() == payload["evidence_digest"]
        assert evidence.experiment_identity_digest == payload["experiment_identity_digest"]
        assert evidence.to_dict()["rankable"] is False
        if name.endswith("nonascii_int.json"):
            assert evidence.experiment_identity["base_instance_id"] == "实例-基线"
            assert evidence.max_numeric_error == 0
            assert evidence.tolerance == 1
        assert canonical


def test_external_fixture_requires_explicit_registered_artifact_root():
    fixture = Path(__file__).parents[1] / "fixtures" / "golden_diagnostic_envelope.json"
    with pytest.raises(DiagnosticEvidenceError, match="escapes registered artifact roots"):
        read_diagnostic_evidence_v3(fixture)
    evidence = read_diagnostic_evidence_v3(
        fixture,
        artifact_roots=(Path("/home/pupil/Documents/Codex/agent-team/workspaces/heterocoord-tool-qualification-round2-20261020/artifacts"),),
    )
    assert evidence.shutdown_status == "pending_timeout"


def test_registered_artifact_root_rejects_symlink(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    alias = tmp_path / "artifact-link"
    alias.symlink_to(root, target_is_directory=True)
    fixture = Path(__file__).parents[1] / "fixtures" / "golden_diagnostic_envelope.json"
    with pytest.raises(DiagnosticEvidenceError, match="cannot be a symlink"):
        read_diagnostic_evidence_v3(fixture, artifact_roots=(alias,))


def test_shutdown_timeout_exit_code_and_scope_cannot_be_relabelled(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures" / "golden_diagnostic_envelope.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    root = Path("/home/pupil/Documents/Codex/agent-team/workspaces/heterocoord-tool-qualification-round2-20261020/artifacts")

    def write_variant(name, *, shutdown_status=None, process_failure_scope=None):
        value = json.loads(json.dumps(payload))
        if shutdown_status is not None:
            value["shutdown_status"] = shutdown_status
            value["provenance"]["shutdown_status"] = shutdown_status
        if process_failure_scope is not None:
            value["process_failure_scope"] = process_failure_scope
            value["provenance"]["process_failure_scope"] = process_failure_scope
        value["provenance_digest"] = canonical_digest(value["provenance"])
        value["evidence_digest"] = canonical_digest({
            key: item for key, item in value.items()
            if key not in {"provenance", "provenance_digest", "evidence_digest"}
        })
        path = tmp_path / name
        path.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return path

    with pytest.raises(DiagnosticEvidenceError, match="clean shutdown"):
        read_diagnostic_evidence_v3(write_variant("clean.json", shutdown_status="clean"), artifact_roots=(root,))
    with pytest.raises(DiagnosticEvidenceError, match="process_failure_scope"):
        read_diagnostic_evidence_v3(
            write_variant("single-process.json", process_failure_scope="single_reference_process"),
            artifact_roots=(root,),
        )
    sized = json.loads(json.dumps(payload))
    sized["provenance"]["output_size"] = 0
    sized["provenance_digest"] = canonical_digest(sized["provenance"])
    sized_path = tmp_path / "bad-size.json"
    sized_path.write_text(json.dumps(sized, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(DiagnosticEvidenceError, match="output_size"):
        read_diagnostic_evidence_v3(sized_path, artifact_roots=(root,))
