"""Source-bound ingestion for explicitly authored deployment contracts.

Hashes establish which inputs were checked, not the truth of their claims or
semantic equivalence between free-text catalog rules and a machine contract.
No authoring prose is compiled into constraints here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .oracle_clock_v3 import seconds_to_ticks
from .scorebook_v3 import PAPER_COMMIT
from .task_catalog import TaskAnchor, validate_catalog


SCHEMA = "heterocoord.canonical_package.v3"
MAX_INPUT_BYTES = 8 * 1024 * 1024


def _json(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"nonfinite JSON value: {value}")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _read(path: Path, root: Path) -> bytes:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("canonical package sources must resolve inside the project")
    with resolved.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("canonical package source exceeds input byte limit")
    return raw


@dataclass(frozen=True)
class BoundCanonicalPackageV3:
    """Detached checked bytes; parsing the body remains a separate required step."""
    package_sha256: str
    catalog_sha256: str
    scenario_sha256: str
    instance_id: str
    anchor_id: str
    scene_key: str
    anchor_horizon_ticks: int
    anchor_obligations: tuple[str, ...]
    anchor_roles: tuple[str, ...]
    anchor_capabilities: tuple[str, ...]
    _contract_json: str

    @property
    def contract_mapping(self) -> dict[str, Any]:
        return json.loads(self._contract_json)

    def parse_contract(self):
        from .canonical_deployment_v3 import from_mapping
        contract = from_mapping(self.contract_mapping)
        if contract.task_id != self.instance_id:
            raise ValueError("contract task_id differs from bound instance")
        if contract.horizon > self.anchor_horizon_ticks:
            raise ValueError("contract horizon exceeds catalog anchor")
        if set(contract.obligation_ids) != set(self.anchor_obligations):
            raise ValueError("contract obligations differ from catalog anchor")
        roles = {role for obligation in contract.obligations for role in obligation.role_requirements}
        capabilities = {cap for obligation in contract.obligations for cap in obligation.required_capabilities}
        if roles != set(self.anchor_roles) or capabilities != set(self.anchor_capabilities):
            raise ValueError("contract roles or capabilities differ from catalog anchor")
        return contract

    def provenance(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "paper_commit": PAPER_COMMIT,
            "package_sha256": self.package_sha256,
            "catalog_sha256": self.catalog_sha256,
            "scenario_sha256": self.scenario_sha256,
            "instance_id": self.instance_id, "anchor_id": self.anchor_id,
            "scene_key": self.scene_key,
            "source_binding_checked": True,
            "catalog_semantic_equivalence_verified": False,
            "physical_qualification_verified": False,
        }


def load_canonical_package(
    package_path: str | Path, *, expected_package_sha256: str,
    expected_instance_id: str, catalog_path: str | Path, scenario_path: str | Path,
    project_root: str | Path = Path(__file__).resolve().parents[1],
) -> BoundCanonicalPackageV3:
    """Check external package pin and recompute all source digests from bytes.

    The caller freezes the expected package hash before evaluation. A valid
    binding never changes a catalog anchor's certification status.
    """
    if not _sha(expected_package_sha256) or not isinstance(expected_instance_id, str) or not expected_instance_id.strip():
        raise ValueError("external package hash and expected instance identity required")
    root = Path(project_root).resolve(strict=True)
    raw = _read(Path(package_path), root)
    if _digest(raw) != expected_package_sha256:
        raise ValueError("external package hash mismatch")
    package = _json(raw)
    fields = {"schema", "paper_commit", "instance_id", "anchor_id", "scene_key",
              "catalog_sha256", "scenario_sha256", "contract"}
    if not isinstance(package, dict) or set(package) != fields:
        raise ValueError("canonical package requires exactly the registered fields")
    if package["schema"] != SCHEMA or package["paper_commit"] != PAPER_COMMIT:
        raise ValueError("canonical package schema or frozen paper mismatch")
    if package["instance_id"] != expected_instance_id:
        raise ValueError("canonical package instance mismatch")
    catalog_raw = _read(Path(catalog_path), root)
    scenario_raw = _read(Path(scenario_path), root)
    for name, content in (("catalog_sha256", catalog_raw), ("scenario_sha256", scenario_raw)):
        if not _sha(package[name]) or package[name] != _digest(content):
            raise ValueError(f"{name} source mismatch")
    catalog_data, scenario = _json(catalog_raw), _json(scenario_raw)
    if not isinstance(catalog_data, dict) or not isinstance(catalog_data.get("anchors"), list):
        raise ValueError("catalog must contain anchors")
    if not isinstance(scenario, dict) or not scenario:
        raise ValueError("scenario source must be a nonempty JSON object")
    anchors = validate_catalog(TaskAnchor.from_mapping(item) for item in catalog_data["anchors"])
    matches = [anchor for anchor in anchors if anchor.anchor_id == package["anchor_id"]]
    if len(matches) != 1 or package["scene_key"] != matches[0].scene_key:
        raise ValueError("package anchor or scene does not match catalog")
    anchor = matches[0]
    body = package["contract"]
    if not isinstance(body, dict) or not body:
        raise ValueError("explicit machine contract required; catalog prose is insufficient")
    return BoundCanonicalPackageV3(
        expected_package_sha256, package["catalog_sha256"], package["scenario_sha256"],
        expected_instance_id, anchor.anchor_id, anchor.scene_key,
        seconds_to_ticks(anchor.horizon_s, rounding="floor"),
        tuple(anchor.obligations), tuple(anchor.roles), tuple(anchor.required_capabilities),
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False),
    )


def catalog_input_inventory(*, catalog_path: str | Path) -> dict[str, Any]:
    """Inventory actual catalog inputs, without generating fictional instances."""
    raw = Path(catalog_path).read_bytes()
    data = _json(raw)
    anchors = validate_catalog(TaskAnchor.from_mapping(item) for item in data["anchors"])
    return {
        "schema": "heterocoord.canonical_input_inventory.v3",
        "paper_commit": PAPER_COMMIT, "catalog_sha256": _digest(raw),
        "anchor_count": len(anchors),
        "scope": "catalog_fields_only_no_sidecar_discovery",
        "anchors": [{
            "anchor_id": a.anchor_id, "scene_key": a.scene_key,
            "horizon_ticks": seconds_to_ticks(a.horizon_s, rounding="floor"),
            "obligations": list(a.obligations), "roles": list(a.roles),
            "certification_status": a.certification_status,
            "required_instance_inputs": [
                "frozen_robot_pool_and_capability_matrix", "role_compatibility_and_rules",
                "obligation_integer_timing_and_coalitions", "resource_capacities_and_demands",
                "registered_precedence_handoff_and_mutex_rules", "bound_scenario_and_machine_contract",
            ],
        } for a in anchors],
    }
