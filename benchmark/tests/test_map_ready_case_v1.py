from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from benchmark import map_ready_case_v1 as orchestrator
from benchmark.case_manifest_v1 import bind_case_manifest
from benchmark.map_ready_case_v1 import MapReadyCaseError, run_map_ready_case
from benchmark.records_v3 import canonical_digest
from benchmark.evaluator.room06_livt04 import (
    EVALUATOR_ID,
    EVALUATOR_VERSION,
    build_diagnostic_runtime_fixture,
)
from tools.benchmark_branch.models import BranchJob


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _catalog() -> dict:
    return {"schema": "catalog.v1", "anchors": [{"anchor_id": "liv-t-04"}]}


def _contract() -> dict:
    artifact = json.loads(
        (
            PROJECT_ROOT
            / "artifacts/catalog/contracts/room06-extension-20261026/pilot_contracts-v3.json"
        ).read_text(encoding="utf-8")
    )
    return next(
        dict(row) for row in artifact["contracts"] if row["anchor_id"] == "liv-t-04"
    )


def _binding(plan: dict, catalog: dict | None = None, contract: dict | None = None) -> dict:
    catalog = _catalog() if catalog is None else catalog
    contract = _contract() if contract is None else contract
    return {
        "case_id": "room06-livt04-r1",
        "anchor_id": "liv-t-04",
        "plan_digest": canonical_digest(plan),
        "catalog_digest": canonical_digest(catalog),
        "contract": {"contract_digest": canonical_digest(contract)},
        "semantic_requirements": {"semantic_points": ["floor1-bookshelf"]},
    }


def test_missing_binding_is_recorded_fail_closed(tmp_path: Path) -> None:
    output = tmp_path / "attempt-01"
    result = run_map_ready_case(
        semantic_root=tmp_path / "map",
        robot_pool=tmp_path / "pool.json",
        binding=tmp_path / "missing-binding.json",
        catalog=_catalog(),
        contract_artifact=_contract(),
        plan={"schema": "plan"},
        output_dir=output,
    )

    assert result["status"] == "failed_closed"
    assert result["formal_gates"] == orchestrator.FORMAL_GATES
    assert "binding is not a regular JSON file" in result["failures"][0]["detail"]
    assert json.loads((output / "execution_receipt.json").read_text()) == result


def test_missing_map_is_recorded_fail_closed(tmp_path: Path) -> None:
    plan = {"schema": "explicit-plan.v1", "runs": []}
    catalog = _catalog()
    contract = _contract()
    output = tmp_path / "attempt-02"
    result = run_map_ready_case(
        semantic_root=tmp_path / "missing-map",
        robot_pool=tmp_path / "missing-pool.json",
        binding=_binding(plan, catalog, contract),
        catalog=catalog,
        contract_artifact=contract,
        plan=plan,
        output_dir=output,
    )

    assert result["status"] == "failed_closed"
    assert result["audit"] is None
    assert result["manifest"] is None
    assert result["formal_gates"]["paper_score_eligible"] is False
    assert "cannot read semantic map manifest" in result["failures"][0]["detail"]


def test_audit_then_manifest_uses_engineering_paper_and_is_python_api_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"schema": "explicit-plan.v1", "runs": ["r1"]}
    catalog = _catalog()
    contract = _contract()
    binding = _binding(plan, catalog, contract)
    call_order: list[str] = []

    def fake_audit(semantic_root, robot_pool, *, semantic_requirements):
        call_order.append("audit")
        assert semantic_requirements == binding["semantic_requirements"]
        return {
            "case_ready": True,
            "failures": [],
            "semantic_map": {"case_ready": True, "verified_files": {"manifest.json": "1" * 64}},
            "robot_pool": {"case_ready": True, "pool_digest": "2" * 64},
        }

    def fake_build(*, semantic_root, robot_pool_path, binding):
        call_order.append("manifest")
        return {
            "case_id": binding["case_id"],
            "manifest_digest": "3" * 64,
        }

    monkeypatch.setattr(orchestrator, "audit_case_inputs", fake_audit)
    monkeypatch.setattr(orchestrator, "build_case_manifest", fake_build)
    output = tmp_path / "successful-attempt"
    result = run_map_ready_case(
        semantic_root=tmp_path / "map",
        robot_pool=tmp_path / "pool.json",
        binding=binding,
        catalog=catalog,
        contract_artifact=contract,
        plan=plan,
        output_dir=output,
        # The caller expectation agrees but still cannot replace the imported
        # engineering identity.
        paper_commit=orchestrator.TARGET_PAPER_COMMIT,
    )

    assert call_order == ["audit", "manifest"]
    assert result["status"] == "ready_for_terminal_python_api"
    assert result["manifest"]["manifest_digest"] == "3" * 64
    assert result["formal_gates"] == {
        "rankable": False,
        "paper_score_eligible": False,
        "physical_qualified": False,
    }
    assert result["paper_commit"] == orchestrator.ENGINEERING_PAPER_COMMIT
    assert result["paper_commit_source"] == "engineering_scorebook"
    assert result["requested_paper_commit"] == orchestrator.TARGET_PAPER_COMMIT
    assert result["paper_aligned"] is True
    assert result["requested_paper_aligned"] is True
    assert "ready_for_terminal_python_api" in result["next_action"]
    assert "paper_alignment_pending" not in result["next_action"]
    assert not (output / "terminal_evidence").exists()
    assert not (output / "production_results").exists()


@pytest.mark.parametrize(
    "requested",
    [
        "0b006d4efcd25d5160d4b0a6e75cddd9e5265e44",
        "1f329494e755523692f5f96c8e0889c6cec15a82",
    ],
)
def test_stale_caller_paper_is_rejected_without_overriding_engineering_identity(
    tmp_path: Path, requested: str
) -> None:
    output = tmp_path / requested[:8]
    result = run_map_ready_case(
        semantic_root=tmp_path / "map",
        robot_pool=tmp_path / "pool.json",
        binding={},
        catalog={},
        contract_artifact={},
        plan={},
        output_dir=output,
        paper_commit=requested,
    )

    assert result["status"] == "failed_closed"
    assert result["paper_commit"] == orchestrator.ENGINEERING_PAPER_COMMIT
    assert result["requested_paper_commit"] == requested
    assert result["paper_aligned"] is True
    assert result["requested_paper_aligned"] is False
    assert "cannot override" in result["failures"][0]["detail"]
    assert result["formal_gates"] == orchestrator.FORMAL_GATES


def test_stale_contract_paper_is_rejected_before_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"schema": "explicit-plan.v1"}
    catalog = _catalog()
    contract = _contract()
    contract["paper_sha"] = "0b006d4efcd25d5160d4b0a6e75cddd9e5265e44"
    binding = _binding(plan, catalog, contract)
    monkeypatch.setattr(
        orchestrator,
        "audit_case_inputs",
        lambda *_args, **_kwargs: {
            "case_ready": True,
            "failures": [],
            "semantic_map": {},
            "robot_pool": {"pool_digest": "2" * 64},
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "build_case_manifest",
        lambda **_kwargs: pytest.fail("manifest must not use a stale contract"),
    )
    result = run_map_ready_case(
        semantic_root=tmp_path / "map",
        robot_pool=tmp_path / "pool.json",
        binding=binding,
        catalog=catalog,
        contract_artifact=contract,
        plan=plan,
        output_dir=tmp_path / "stale-contract",
    )
    assert result["status"] == "failed_closed"
    assert "contract paper identity is stale" in result["failures"][0]["detail"]
    assert result["formal_gates"] == orchestrator.FORMAL_GATES


def test_digest_matching_self_certified_contract_is_rejected_before_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"schema": "explicit-plan.v1"}
    catalog = _catalog()
    contract = _contract()
    contract["status"] = "certified"
    contract["certification"] = {
        **contract["certification"],
        "case_certified": True,
        "paper_score_eligible": True,
    }
    contract["oracle_contract"] = {
        **contract["oracle_contract"],
        "binding_status": "certified",
    }
    binding = _binding(plan, catalog, contract)
    monkeypatch.setattr(
        orchestrator,
        "audit_case_inputs",
        lambda *_args, **_kwargs: {
            "case_ready": True,
            "failures": [],
            "semantic_map": {},
            "robot_pool": {"pool_digest": "2" * 64},
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "build_case_manifest",
        lambda **_kwargs: pytest.fail("manifest must not use a self-certified contract"),
    )
    result = run_map_ready_case(
        semantic_root=tmp_path / "map",
        robot_pool=tmp_path / "pool.json",
        binding=binding,
        catalog=catalog,
        contract_artifact=contract,
        plan=plan,
        output_dir=tmp_path / "self-certified-contract",
    )
    assert result["status"] == "failed_closed"
    assert "failed pending-contract validation" in result["failures"][0]["detail"]
    assert result["formal_gates"] == orchestrator.FORMAL_GATES


def test_rehashed_certified_contract_set_is_rejected_before_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"schema": "explicit-plan.v1"}
    catalog = _catalog()
    contract = _contract()
    contract_artifact = {
        "schema_version": "heterocoord-pilot-contract-set.v1",
        "status": "certified",
        "paper_sha": contract["paper_sha"],
        "candidate_count": 1,
        "oracle_source_commits": ["cf839ec8", "0b6890c0"],
        "contracts": [contract],
    }
    binding = _binding(plan, catalog, contract)
    monkeypatch.setattr(
        orchestrator,
        "audit_case_inputs",
        lambda *_args, **_kwargs: {
            "case_ready": True,
            "failures": [],
            "semantic_map": {},
            "robot_pool": {"pool_digest": "2" * 64},
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "build_case_manifest",
        lambda **_kwargs: pytest.fail("manifest must not use a certified contract set"),
    )
    result = run_map_ready_case(
        semantic_root=tmp_path / "map",
        robot_pool=tmp_path / "pool.json",
        binding=binding,
        catalog=catalog,
        contract_artifact=contract_artifact,
        plan=plan,
        output_dir=tmp_path / "certified-contract-set",
    )
    assert result["status"] == "failed_closed"
    assert "contract set must remain candidate_contract_only" in result["failures"][0]["detail"]
    assert result["formal_gates"] == orchestrator.FORMAL_GATES


def test_explicit_plan_digest_mismatch_fails_before_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "audit_case_inputs",
        lambda *_args, **_kwargs: {
            "case_ready": True,
            "failures": [],
            "semantic_map": {},
            "robot_pool": {"pool_digest": "2" * 64},
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "build_case_manifest",
        lambda **_kwargs: pytest.fail("manifest must not be built from a stale plan"),
    )
    result = run_map_ready_case(
        semantic_root=tmp_path / "map",
        robot_pool=tmp_path / "pool.json",
        binding={
            "anchor_id": "liv-t-04",
            "catalog_digest": canonical_digest(_catalog()),
            "plan_digest": "0" * 64,
            "contract": {"contract_digest": canonical_digest(_contract())},
        },
        catalog=_catalog(),
        contract_artifact=_contract(),
        plan={"schema": "different-plan"},
        output_dir=tmp_path / "stale-plan-attempt",
    )
    assert result["status"] == "failed_closed"
    assert "explicit plan digest does not match" in result["failures"][0]["detail"]


def test_stale_catalog_digest_fails_before_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"schema": "explicit-plan.v1"}
    catalog = _catalog()
    contract = _contract()
    binding = _binding(plan, catalog, contract)
    binding["catalog_digest"] = "9" * 64
    monkeypatch.setattr(
        orchestrator,
        "audit_case_inputs",
        lambda *_args, **_kwargs: {
            "case_ready": True,
            "failures": [],
            "semantic_map": {},
            "robot_pool": {"pool_digest": "2" * 64},
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "build_case_manifest",
        lambda **_kwargs: pytest.fail("manifest must not be built from a stale catalog"),
    )

    result = run_map_ready_case(
        semantic_root=tmp_path / "map",
        robot_pool=tmp_path / "pool.json",
        binding=binding,
        catalog=catalog,
        contract_artifact=contract,
        plan=plan,
        output_dir=tmp_path / "stale-catalog-attempt",
    )

    assert result["status"] == "failed_closed"
    assert "explicit catalog digest does not match" in result["failures"][0]["detail"]


def test_existing_output_directory_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(MapReadyCaseError, match="already exists"):
        run_map_ready_case(
            semantic_root=tmp_path / "map",
            robot_pool=tmp_path / "pool.json",
            binding={},
            catalog={},
            contract_artifact={},
            plan={},
            output_dir=output,
        )
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (output / "execution_receipt.json").exists()


def test_serialized_terminal_rehydrates_only_registered_job_and_evaluator() -> None:
    plan = {
        "oracle_obligation_binding": {
            "schema": "heterocoord.oracle-obligation-binding.v1",
            "anchor_id": "liv-t-04",
            "obligations": [],
        }
    }
    case = {
        "task_id": "liv-t-04",
        "anchor_id": "liv-t-04",
        "scene": {
            "scene_id": "room_06",
            "horizon": 15000,
            "entity_ids": ["book_batch_01", "heavy_object"],
            "zone_ids": [],
        },
        "evaluator": {
            "evaluator_id": EVALUATOR_ID,
            "evaluator_version": EVALUATOR_VERSION,
        },
    }
    terminal = {
        "job": {
            "run_id": "room06-measured-run",
            "system_id": "emos-zero",
            "scene_key": "room_06",
            "task_id": "liv-t-04",
            "condition_id": "C0",
            "slot": 0,
            "branch_type": "fact",
            "branch_id": "room06-r1",
            "pair_group_id": "room06-r1",
            "repeat_id": "r1",
            "execution_scope": "measured",
        },
        "runtime": {
            "room06_livt04": {
                "episode": {
                    "condition_id": "C0",
                    "branch": "fact",
                    "repeat_id": "r1",
                    "system_id": "emos-zero",
                    "base_instance_id": "room06-r1/base",
                }
            }
        },
        "evaluator_result": {"status": "valid_terminal"},
    }

    normalized = orchestrator._normalize_terminals(
        [terminal], case_manifest=case, plan=plan
    )
    assert isinstance(normalized[0]["job"], BranchJob)
    assert normalized[0]["job"].execution_scope == "measured"
    assert normalized[0]["evaluator"].evaluator_id == EVALUATOR_ID

    terminal["job"]["caller_python_class"] = "arbitrary.module:Class"
    with pytest.raises(ValueError, match="unsupported fields"):
        orchestrator._normalize_terminals([terminal], case_manifest=case, plan=plan)

    del terminal["job"]["caller_python_class"]
    terminal["runtime"]["room06_livt04"]["episode"]["condition_id"] = "C1"
    with pytest.raises(ValueError, match="does not match the serialized BranchJob"):
        orchestrator._normalize_terminals([terminal], case_manifest=case, plan=plan)


def test_serialized_terminal_runs_registered_evaluator_to_case_readiness(
    tmp_path: Path,
) -> None:
    obligation_binding = {
        "schema": "heterocoord.oracle-obligation-binding.v1",
        "anchor_id": "liv-t-04",
        "obligations": [{
            "obligation_id": "relay",
            "relation": "P2",
            "predicate_ids": ["upper_shelf_delivery_after_transition_handoff"],
        }],
    }
    catalog = {"anchors": ["liv-t-04"]}
    plan = {"seed": 20261027, "oracle_obligation_binding": obligation_binding}
    case = bind_case_manifest({
        "schema": "heterocoord.case-manifest.v1",
        "case_id": "room06-livt04-serialized-diagnostic",
        "task_id": "liv-t-04",
        "anchor_id": "liv-t-04",
        "domain": "living_public",
        "relation_signature": ["P2", "P3-J"],
        "base_instance_id": "room06-serialized-r1/base",
        "scene": {
            "scene_id": "room_06",
            "scene_digest": "1" * 64,
            "semantic_map_digest": "2" * 64,
            "topology_digest": "3" * 64,
            "frame": "world",
            "units": "meters+ticks",
            "entity_ids": ["book_batch_01", "heavy_object"],
            "zone_ids": [],
            "horizon": 15000,
            "tau_stable": 25,
        },
        "robot_pool": {
            "pool_id": "diagnostic-pool",
            "roster_digest": "4" * 64,
            "robots": [{
                "robot_id": robot_id,
                "asset_digest": "5" * 64,
                "capability_digest": "6" * 64,
                "initial_state_digest": "7" * 64,
            } for robot_id in ("carrier-0", "carrier-1")],
        },
        "contract": {
            "contract_digest": "8" * 64,
            "oracle_id": "liv-t-04-oracle-v3",
            "contract_revision": "diagnostic-2026-10-27",
        },
        "catalog_digest": canonical_digest(catalog),
        "plan_digest": canonical_digest(plan),
        "replay": {
            "serializer_version": "diagnostic-v1",
            "rng_stream_key": "room06-serialized-diagnostic",
            "checkpoint_schema": "diagnostic-v1",
        },
        "evaluator": {
            "evaluator_id": EVALUATOR_ID,
            "evaluator_version": EVALUATOR_VERSION,
        },
    })
    job = BranchJob(
        run_id="room06-serialized-run",
        system_id="emos-zero",
        scene_key="room_06",
        task_id="liv-t-04",
        condition_id="C0",
        slot=0,
        branch_type="fact",
        branch_id="room06-serialized-r1",
        pair_group_id="room06-serialized-r1",
        repeat_id="r1",
        execution_scope="measured_diagnostic",
    )
    runtime = build_diagnostic_runtime_fixture()
    runtime["episode"].update({
        "system_id": job.system_id,
        "base_instance_id": job.base_instance_id,
    })
    terminal_payload = json.loads(json.dumps({
        "terminals": [{
            "job": asdict(job),
            "runtime": runtime,
            "evaluator_result": {
                "status": "valid_terminal",
                "completed": True,
                "physical_success": True,
                "terminal_predicate": True,
                "safety_predicate": True,
                "feasibility_predicate": True,
            },
        }],
        "parameters": {"alpha": 0.05},
    }))

    result = orchestrator._terminal_chain(
        terminal_payload,
        output_root=tmp_path,
        case_manifest=case,
        binding={"catalog_digest": canonical_digest(catalog)},
        catalog=catalog,
        plan=plan,
    )

    assert result["status"] == "terminal_sealed"
    assert result["readiness"]["case_receipt_ready"] is True
    assert result["readiness"]["paper_support_ready"] is False
    assert result["formal_gates"] == orchestrator.FORMAL_GATES
    assert len(list((tmp_path / "terminal_evidence" / "evidence").glob("*.json"))) == 1


def test_serialized_terminal_rejects_unregistered_manifest_evaluator() -> None:
    with pytest.raises(ValueError, match="not registered"):
        orchestrator._registered_evaluator(
            {
                "evaluator": {
                    "evaluator_id": "caller.module:Evaluator",
                    "evaluator_version": "v1",
                }
            },
            {"oracle_obligation_binding": {}},
        )


def test_cli_loads_json_terminal_for_registered_sealing_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"schema": "explicit-plan.v1", "runs": ["r1"]}
    catalog = _catalog()
    contract = _contract()
    binding = _binding(plan, catalog, contract)
    plan_path = tmp_path / "plan.json"
    binding_path = tmp_path / "binding.json"
    terminal_path = tmp_path / "terminal.json"
    catalog_path = tmp_path / "catalog.json"
    contract_path = tmp_path / "contract.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    terminal_payload = {"terminals": [{"explicit": "runtime-material"}], "parameters": {}}
    terminal_path.write_text(json.dumps(terminal_payload), encoding="utf-8")
    monkeypatch.setattr(
        orchestrator,
        "audit_case_inputs",
        lambda *_args, **_kwargs: {
            "case_ready": True,
            "failures": [],
            "semantic_map": {},
            "robot_pool": {"pool_digest": "2" * 64},
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "build_case_manifest",
        lambda **_kwargs: {
            "case_id": binding["case_id"],
            "manifest_digest": "3" * 64,
        },
    )
    terminal_calls = []

    def fake_terminal_chain(value, **kwargs):
        terminal_calls.append((value, kwargs))
        assert value == terminal_payload
        return {
            "status": "terminal_sealed",
            "formal_gates": dict(orchestrator.FORMAL_GATES),
        }

    monkeypatch.setattr(orchestrator, "_terminal_chain", fake_terminal_chain)
    output = tmp_path / "cli-terminal-attempt"
    exit_code = orchestrator.main([
        "--semantic-root", str(tmp_path / "map"),
        "--robot-pool", str(tmp_path / "pool.json"),
        "--binding", str(binding_path),
        "--catalog", str(catalog_path),
        "--contract-artifact", str(contract_path),
        "--plan", str(plan_path),
        "--output-dir", str(output),
        "--terminal-payload", str(terminal_path),
    ])

    assert exit_code == 0
    receipt = json.loads((output / "execution_receipt.json").read_text())
    assert receipt["status"] == "terminal_sealed"
    assert receipt["failures"] == []
    assert receipt["inputs"]["terminal_payload_digest_kind"] == "file_sha256"
    assert len(terminal_calls) == 1
    assert terminal_calls[0][1]["case_manifest"]["manifest_digest"] == "3" * 64
    assert not (output / "production_results").exists()
