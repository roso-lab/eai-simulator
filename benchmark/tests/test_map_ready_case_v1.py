from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark import map_ready_case_v1 as orchestrator
from benchmark.map_ready_case_v1 import MapReadyCaseError, run_map_ready_case
from benchmark.records_v3 import canonical_digest


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


def test_cli_json_terminal_is_fail_closed_without_results(
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
    terminal_path.write_text(json.dumps({"terminals": []}), encoding="utf-8")
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

    assert exit_code == 2
    receipt = json.loads((output / "execution_receipt.json").read_text())
    assert receipt["status"] == "failed_closed"
    assert receipt["failures"][0]["code"] == "TERMINAL_CLI_NOT_SERIALIZABLE"
    assert "terminal_cli_not_serializable" in receipt["next_action"]
    assert "ready_for_terminal_python_api" in receipt["next_action"]
    assert not (output / "terminal_evidence").exists()
    assert not (output / "production_results").exists()
