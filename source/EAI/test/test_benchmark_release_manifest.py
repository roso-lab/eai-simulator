"""Suite release manifest keeps design completion separate from deployment."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.provenance import canonical_sha256
from demo.benchmark.release_manifest import (
    audit_challenge_bundle, build_release_manifest,
    compile_default_release_manifest, main,
)
from demo.warehouse_benchmark.challenge import compile_challenge


def test_default_manifest_covers_three_scenes_and_factory_vertical_probe():
    manifest = compile_default_release_manifest()
    assert manifest["release_status"] == "pending"
    assert manifest["benchmark_ready"] is False
    assert {row["package_id"] for row in manifest["packages"]} == {
        "factory_business", "factory_vertical_diagnostic",
        "warehouse_business", "modern_house_business",
    }
    assert {row["scene_key"] for row in manifest["packages"]} == {
        "factory", "warehouse_nvd", "modern_house_nvd",
    }
    assert all(row["contract_publishable"] for row in manifest["packages"])
    assert all(not row["benchmark_ready"] and not row["scoreable"]
               and not row["execution_validated"] and row["blockers"]
               for row in manifest["packages"])
    assert manifest["claims"] == {
        "contract_design_complete": True,
        "full_benchmark_deployed": False,
        "physical_heterogeneous_capability_validated": False,
    }
    assert set(manifest["lifecycle_plans"]) == {
        "factory", "warehouse_nvd", "modern_house_nvd",
    }
    assert all(plan["physical_execution_validated"] is False
               and plan["route"]["validated"]
               for plan in manifest["lifecycle_plans"].values())


def test_audit_records_all_layer_hashes_and_rejects_spoofed_promotion():
    bundle = compile_challenge()
    result = audit_challenge_bundle("warehouse", bundle)
    assert result["hashes"] == {
        name: canonical_sha256(bundle[name]) for name in (
            "public_task", "participant_api", "baseline_manifest", "hidden_evaluator")
    }

    promoted = deepcopy(bundle)
    promoted["public_task"]["release_status"] = "ready"
    promoted_hash = canonical_sha256(promoted["public_task"])
    for name in ("participant_api", "baseline_manifest", "hidden_evaluator"):
        promoted[name]["release_status"] = "ready"
        promoted[name]["public_task_sha256"] = promoted_hash
    with pytest.raises(ValueError, match="ready release requires"):
        audit_challenge_bundle("warehouse", promoted)


def test_audit_rejects_tampered_binding_and_missing_layer():
    bundle = compile_challenge()
    tampered = deepcopy(bundle)
    tampered["participant_api"]["public_task_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="not bound"):
        audit_challenge_bundle("warehouse", tampered)
    missing = deepcopy(bundle)
    del missing["hidden_evaluator"]
    with pytest.raises(ValueError, match="exactly the four"):
        build_release_manifest({"warehouse": missing})


def test_cli_writes_review_manifest_without_overwriting(tmp_path, capsys):
    output = tmp_path / "release-manifest.json"
    assert main(["--output", str(output)]) == 0
    assert "release_manifest:" in capsys.readouterr().out
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["release_status"] == "pending"
    with pytest.raises(FileExistsError):
        main(["--output", str(output)])
