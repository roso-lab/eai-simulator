"""Read and audit a city preparation directory without promoting it to a bundle.

Only ``public_task``, ``participant_api`` and ``public_maps`` are participant
materials. The returned candidate is an author-only management record. Loading
does not run manifest entrypoints, register skills, repair stale APIs, create
witnesses, launch simulation or write files. ``verify_sources`` checks the bytes
of explicitly listed local sources; it cannot certify provider immutability or
discover missing USD dependencies.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
from typing import Any

from .goal_driven_contract import (
    build_goal_participant_api, validate_goal_participant_api, validate_goal_task_spec,
)
from .provenance import canonical_sha256
from .skill_ontology import skill_names


_KIND = "preparation_draft_not_goal_bundle"
_ARTIFACTS = frozenset({
    "release/public_task.json", "release/participant_api.json",
    "release/maps/occupancy.png", "release/maps/occupancy.yaml",
    "release/maps/map_preview.png", "release/maps/map_metadata.json",
    "release/maps/semantic_map.json", "authoring/private.json",
    "authoring/review.json", "authoring/navigation_config.json",
})
_SOURCE_NAMES = {"city_layout.json", "city_scenario.json", "city_static.usd"}
_CITY_OUTCOMES = {"incident_assessed", "exposed_people_accounted_for",
                  "secondary_traffic_protected", "response_access_preserved"}
_CITY_SERVICE_OUTCOMES = {"guide_people": "exposed_people_accounted_for",
                          "control_traffic": "secondary_traffic_protected",
                          "report_incident": "response_access_preserved"}
_PRIVATE_PUBLIC_KEYS = {
    "source_provenance", "source_sha256", "composed_usd_layers_sha256",
    "runtime_prim_path", "static_prim_bindings", "incident_template", "response_template",
    "private_factor_design", "full_mission_witnesses", "selected_target", "incident_seed",
    "affected_person_roster", "hidden_actor_ids", "private_authoring", "hidden_evaluator",
    "integration_manifest", "authoring_review", "source_binding", "ground_truth",
    "hidden_event_schedule", "reference_assignment", "reference_plan", "witness_plans",
}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _strings(value: Any, label: str, *, empty: bool = False) -> list[str]:
    if (not isinstance(value, list) or (not value and not empty)
            or any(not isinstance(v, str) or not v for v in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{label} must be a unique {'possibly empty' if empty else 'nonempty'} string list")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a lowercase SHA256 digest")
    return value


def _relative(value: Any) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or "\x00" in value or value.startswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError(f"unsafe relative artifact/source path: {value!r}")
    return value


def _absolute(value: Any) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("source layer path must be an absolute local path")
    _relative(value[1:])
    return Path(value)


def _directory(path: Path) -> int:
    """Open each directory component without following symbolic links."""
    path = path.absolute()
    if ".." in path.parts:
        raise ValueError("package/source directory must not contain parent traversal")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise ValueError(f"directory unavailable or symbolic link rejected: {path}") from exc


def _file_bytes(directory: int, name: str) -> bytes:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f"artifact must be a regular file: {name}")
            return stream.read()
    except OSError as exc:
        raise ValueError(f"artifact unavailable or symbolic link rejected: {name}") from exc


def _read_tree(directory: int, prefix: str = "") -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            name = prefix + entry.name
            _relative(name)
            if entry.is_symlink():
                raise ValueError(f"symbolic link rejected in preparation package: {name}")
            if entry.is_dir(follow_symlinks=False):
                if not any(path.startswith(name + "/") for path in _ARTIFACTS):
                    raise ValueError(f"unexpected artifact directory: {name}")
                try:
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                except OSError as exc:
                    raise ValueError(f"artifact directory unavailable or symbolic link rejected: {name}") from exc
                try:
                    files.update(_read_tree(child, name + "/"))
                finally:
                    os.close(child)
            else:
                if name not in _ARTIFACTS | {"integration_manifest.json"}:
                    raise ValueError(f"unexpected artifact file: {name}")
                files[name] = _file_bytes(directory, entry.name)
    return files


def _json(data: bytes, label: str) -> dict[str, Any]:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label}: duplicate JSON key {key}")
            value[key] = item
        return value

    def nonfinite(value):
        raise ValueError(f"{label}: nonfinite JSON constant {value}")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            nonfinite(value)
        return result

    try:
        result = json.loads(data, object_pairs_hook=unique, parse_constant=nonfinite, parse_float=finite_float)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact: {label}") from exc
    return _object(result, label)


def _schema(row: dict[str, Any], name: str) -> None:
    if row.get("schema") != name or type(row.get("version")) is not int or row["version"] != 1:
        raise ValueError(f"unsupported city artifact schema: {name}")


def _source_binding(private: dict[str, Any], verify: bool) -> dict[str, Any]:
    provenance = _object(private.get("source_provenance"), "source_provenance")
    sources = _object(provenance.get("source_sha256"), "source_sha256")
    layers = _object(provenance.get("composed_usd_layers_sha256"), "composed_usd_layers_sha256")
    for name, digest in sources.items():
        _relative(name)
        _digest(digest, f"source_sha256[{name}]")
    if set(sources) != _SOURCE_NAMES:
        raise ValueError("source_sha256 must cover exactly city layout, scenario and static USD")
    for name, digest in layers.items():
        _absolute(name)
        _digest(digest, "composed USD layer digest")
    roots = [Path(name) for name in layers if PurePosixPath(name).name == "city_static.usd"]
    if len(roots) != 1 or layers[str(roots[0])] != sources["city_static.usd"]:
        raise ValueError("source provenance needs one matching composed city_static.usd root")
    source_root = roots[0].parent
    checked = 0
    if verify:
        paths = {str(source_root / name): digest for name, digest in sources.items()}
        for name, digest in layers.items():
            if name in paths and paths[name] != digest:
                raise ValueError("conflicting source and composed-layer digest")
            paths[name] = digest
        for name, expected in sorted(paths.items()):
            path = _absolute(name)
            directory = _directory(path.parent)
            try:
                actual = hashlib.sha256(_file_bytes(directory, path.name)).hexdigest()
            finally:
                os.close(directory)
            if actual != expected:
                raise ValueError(f"source SHA256 mismatch: {name}")
            checked += 1
    return {
        "kind": "declared_local_file_hashes", "verification_requested": verify,
        "status": "listed_local_bytes_verified" if verify else "local_sources_not_checked",
        "source_root": str(source_root), "source_sha256": deepcopy(sources),
        "composed_usd_layers_sha256": deepcopy(layers), "checked_file_count": checked,
        "immutable_provider_verified": False, "usd_dependency_closure_verified": False,
        "scene_geometry_validated": False,
        "declared_layout_digest_verified": False,
    }


def _private_tokens(private: dict[str, Any], source: dict[str, Any]) -> set[str]:
    tokens = {source["source_root"], *source["composed_usd_layers_sha256"]}
    for row in private.get("static_prim_bindings", []):
        if isinstance(row, dict) and isinstance(row.get("runtime_prim_path"), str):
            tokens.add(row["runtime_prim_path"])
    template = private.get("response_template")
    if isinstance(template, dict):
        for group in ("people", "vehicles", "actors"):
            for row in template.get(group, []):
                if isinstance(row, dict):
                    tokens.update(row[key] for key in ("id", "person_id", "actor_id", "vehicle_id")
                                  if isinstance(row.get(key), str) and row[key])
    return {token for token in tokens if token}


def _public_boundary(value: Any, private_tokens: set[str], label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _PRIVATE_PUBLIC_KEYS:
                raise ValueError(f"private field leaked into {label}: {key}")
            _public_boundary(key, private_tokens, label)
            _public_boundary(item, private_tokens, label)
    elif isinstance(value, list):
        for item in value:
            _public_boundary(item, private_tokens, label)
    elif isinstance(value, str):
        if (any(token in value for token in private_tokens)
                or any(token in value for token in ("authoring/", "file://", "/home/", "/tmp/", "integration_manifest.json"))):
            raise ValueError(f"private path or actor identity leaked into {label}")


def _maps(blobs: dict[str, bytes], public: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    import yaml

    metadata = _json(blobs["release/maps/map_metadata.json"], "map_metadata")
    semantics = _json(blobs["release/maps/semantic_map.json"], "semantic_map")
    _schema(metadata, "eai.city.static_map")
    _schema(semantics, "eai.city.semantic_map")
    if set(semantics) != {"schema", "version", "coordinate_frame", "meters_per_unit", "up_axis",
                         "regions", "bindings", "static_obstacles", "vehicle_routes",
                         "pedestrian_routes", "surface_heights_m", "scope"}:
        raise ValueError("public semantic map has missing or extra fields")
    for row in (metadata, semantics):
        if (row.get("coordinate_frame") != "city_world" or row.get("meters_per_unit") != 1.0
                or isinstance(row.get("meters_per_unit"), bool) or row.get("up_axis") != "Z"):
            raise ValueError("city map must use city_world metres and Z-up")
    if (metadata.get("status") != "static_geometry_candidate"
            or metadata.get("navigation_validated") is not False):
        raise ValueError("city map must remain an unvalidated static candidate")
    if semantics.get("regions") != public["public_world"]["regions"]:
        raise ValueError("semantic map regions do not match public task")
    if _digest(manifest.get("public_map_semantics_sha256"), "map canonical hash") != canonical_sha256(semantics):
        raise ValueError("canonical semantic map hash mismatch")
    resolution = metadata.get("resolution_m")
    if (isinstance(resolution, bool) or not isinstance(resolution, (int, float))
            or not math.isfinite(resolution) or resolution <= 0):
        raise ValueError("map resolution must be finite and positive")
    if any(type(metadata.get(key)) is not int or metadata[key] < 1 for key in ("width", "height")):
        raise ValueError("map dimensions must be positive integers")
    origin = metadata.get("origin_xyz_m")
    if (not isinstance(origin, list) or len(origin) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in origin)):
        raise ValueError("map origin must be three finite coordinates")
    for name in ("occupancy.png", "map_preview.png"):
        data = blobs[f"release/maps/{name}"]
        if (len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR"
                or struct.unpack(">II", data[16:24]) != (metadata["width"], metadata["height"])):
            raise ValueError(f"PNG map header/dimensions mismatch: {name}")
    # Reject duplicate YAML keys as well as unsafe YAML constructors.
    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            if not isinstance(key, str) or key in result:
                raise ValueError("occupancy YAML requires unique string keys")
            result[key] = loader.construct_object(value_node, deep=True)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        occupancy = _object(yaml.load(blobs["release/maps/occupancy.yaml"], Loader=UniqueLoader), "occupancy YAML")
    except yaml.YAMLError as exc:
        raise ValueError("invalid occupancy YAML") from exc
    if (set(occupancy) != {"image", "resolution", "origin", "negate", "occupied_thresh", "free_thresh"}
            or occupancy["image"] != "occupancy.png" or occupancy["resolution"] != resolution
            or occupancy["origin"] != metadata.get("origin_xyz_m")
            or occupancy["negate"] != 0 or occupancy["occupied_thresh"] != .65
            or occupancy["free_thresh"] != .196):
        raise ValueError("occupancy YAML must reference the packaged map and agree with metadata")
    return {"metadata": metadata, "semantic_map": semantics, "occupancy_config": occupancy,
            "image_artifacts": {name: manifest["artifact_sha256"][name] for name in sorted(_ARTIFACTS)
                                if name.endswith(".png")},
            "image_validation_scope": "file_sha256_and_png_header_dimensions_not_pixel_or_collision_validation"}


def _service_review(private: dict[str, Any], public: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    from .goal_service_catalog import review_service_requirements

    required = private.get("required_services")
    if not isinstance(required, list) or not required:
        raise ValueError("private authoring must declare required_services")
    shared_review = review_service_requirements(required, public)
    by_id = {row.get("id"): row for row in required if isinstance(row, dict)}
    if any(name not in by_id or outcome not in by_id[name].get("outcome_ids", [])
           for name, outcome in _CITY_SERVICE_OUTCOMES.items()):
        raise ValueError("city required_services must retain guidance, traffic and reporting outcome bindings")
    identifiers = []
    proposals = []
    outcomes = {row["id"] for row in public["required_outcomes"]}
    for raw in required:
        row = _object(raw, "required service")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("required service IDs must be unique nonempty strings")
        if not isinstance(row.get("status"), str) or not row["status"]:
            raise ValueError("required service must disclose its proposal status")
        targets = _strings(row.get("outcome_ids"), "service outcome_ids")
        if not set(targets) <= outcomes:
            raise ValueError("required service refers to unknown outcomes")
        identifiers.append(identifier)
        proposals.append({"service_id": identifier, "declared_status": row["status"],
                          "outcome_ids": list(targets), "execution_validated": False})
    shared = set(skill_names())
    required_public_skills = {
        row["required_public_skill"] for row in required
        if isinstance(row, dict) and isinstance(row.get("required_public_skill"), str)
    }
    declared = {skill for robot in public["candidate_robot_pool"] for skill in robot["public_capabilities"]}
    service_by_id = {
        row["id"]: row for row in required
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    registered_service_ids = sorted(
        service_id for service_id, row in service_by_id.items()
        if row.get("required_public_skill") in shared)
    missing_service_ids = sorted(
        service_id for service_id, row in service_by_id.items()
        if row.get("required_public_skill") not in shared)
    return {
        "required_service_ids": sorted(identifiers),
        "registered_shared_skill_ids": sorted(required_public_skills & shared),
        "registered_service_ids": registered_service_ids,
        "missing_service_ids": missing_service_ids,
        "publicly_declared_service_ids": sorted(
            service_id for service_id, row in service_by_id.items()
            if row.get("required_public_skill") in declared),
        "local_backend_proposals": proposals,
        "declared_backend_entrypoints": {key: manifest[key] for key in (
            "response_services", "human_response_binding", "following_traffic_runtime", "incident_report_runtime")
                                        if isinstance(manifest.get(key), str)},
        "backend_availability": "manifest_declarations_not_imported_or_execution_validated",
        "local_backend_is_shared_skill": False, "execution_validated": False,
        "shared_service_review": shared_review,
    }


def _factor_inputs(public: dict[str, Any], private: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    design = _object(private.get("private_factor_design"), "private_factor_design")
    parameters = _strings(private.get("required_episode_parameters"), "required_episode_parameters")
    frozen_public = deepcopy(public)
    frozen_public.pop("experiment_factor")
    frozen = {
        "public_task_except_factor": frozen_public,
        "map_artifact_sha256": {name: digest for name, digest in manifest["artifact_sha256"].items()
                                if name.startswith("release/maps/")},
        "source_provenance": private["source_provenance"],
        "incident_template": private.get("incident_template"),
        "response_template": private.get("response_template"),
        "required_episode_parameters": parameters,
    }
    return {
        "visibility": "author_only", "status": "candidate_design_not_instantiated",
        "public_task_sha256": canonical_sha256(public), "frozen_input_sha256": canonical_sha256(frozen),
        "factor_axes": {"communication_quality": ["immediate", "delayed", "dropped", "bandwidth_limited", "stale"],
                        "team_continuity": ["stable", "member_exit", "member_join", "capability_change"]},
        "private_design": deepcopy(design), "episode_parameters_to_bind": list(parameters),
        "hidden_treatments_instantiated": False, "factor_effect_validated": False,
        "requires_complete_business_witnesses": True,
    }


def load_city_goal_candidate(package_dir: str | Path, *, verify_sources: bool = False) -> dict[str, Any]:
    """Load an exact city prepare v1 directory, rejecting invalid/stale inputs.

    Missing services are a reported authoring blocker, not a parser error. File
    integrity, information-boundary and shared-contract failures raise ValueError.
    No input is repaired or written. Only listed local source bytes are checked
    when ``verify_sources=True``; no provider, USD, ROS or runtime is contacted.
    """
    if type(verify_sources) is not bool:
        raise ValueError("verify_sources must be a boolean")
    root = Path(package_dir).absolute()
    directory = _directory(root)
    try:
        blobs = _read_tree(directory)
    finally:
        os.close(directory)
    if set(blobs) != _ARTIFACTS | {"integration_manifest.json"}:
        raise ValueError(f"missing artifact files: {sorted((_ARTIFACTS | {'integration_manifest.json'}) - set(blobs))}")
    manifest = _json(blobs["integration_manifest.json"], "integration_manifest")
    _schema(manifest, "eai.city.integration_manifest")
    if (manifest.get("scene_key") != "city" or manifest.get("package_kind") != _KIND
            or manifest.get("task_family") != "urban_incident_response"):
        raise ValueError("manifest must identify city preparation draft, not a builder scaffold or goal bundle")
    hashes = _object(manifest.get("artifact_sha256"), "artifact_sha256")
    for name, expected in hashes.items():
        _relative(name)
        _digest(expected, f"artifact_sha256[{name}]")
    if set(hashes) != _ARTIFACTS:
        raise ValueError("artifact_sha256 inventory has missing or extra entries")
    for name, expected in hashes.items():
        if hashlib.sha256(blobs[name]).hexdigest() != expected:
            raise ValueError(f"artifact SHA256 mismatch: {name}")
    if (manifest.get("public_inputs") != ["release/public_task.json", "release/participant_api.json", "release/maps/"]
            or manifest.get("private_inputs") != ["authoring/private.json", "authoring/review.json"]):
        raise ValueError("manifest public/private inputs do not match the preparation boundary")
    if (manifest.get("release_status") != "authoring_draft"
            or manifest.get("execution_validated") is not False or manifest.get("scoreable") is not False):
        raise ValueError("city preparation manifest cannot claim execution or scoring readiness")

    public = _json(blobs["release/public_task.json"], "public_task")
    api = _json(blobs["release/participant_api.json"], "participant_api")
    if _object(public.get("mission"), "public mission").get("scene_key") != "city":
        raise ValueError("public task scene identity must be city, not modern_house_nvd scaffold")
    try:
        validate_goal_task_spec(public)
        if {row["id"] for row in public["required_outcomes"]} != _CITY_OUTCOMES:
            raise ValueError("city preparation must retain the urban incident-response outcome family")
        validate_goal_participant_api(api, task_spec=public)
        if api != build_goal_participant_api(public):
            raise ValueError("participant API differs from the current shared declaration")
    except ValueError as exc:
        raise ValueError(f"stale or invalid shared city public/API contract; rerun city prepare: {exc}") from exc
    public_hash = canonical_sha256(public)
    if _digest(manifest.get("public_task_sha256"), "manifest public task hash") != public_hash:
        raise ValueError("manifest canonical public task hash mismatch")
    private = _json(blobs["authoring/private.json"], "private authoring")
    review = _json(blobs["authoring/review.json"], "authoring review")
    navigation = _json(blobs["authoring/navigation_config.json"], "navigation handoff")
    _schema(private, "eai.city.authoring_private")
    _schema(review, "eai.city.authoring_review")
    _schema(navigation, "eai.city.navigation_handoff")
    if private.get("task_id") != public["task_id"] or private.get("public_task_sha256") != public_hash:
        raise ValueError("private authoring canonical public task binding mismatch")
    if private.get("outcome_evidence_requirements") != public["required_outcomes"]:
        raise ValueError("private outcome evidence requirements differ from public outcomes")
    if private.get("full_mission_witnesses") != []:
        raise ValueError("preparation intake does not accept full mission witnesses or a hidden evaluator")
    if (review.get("authoring_status") != "blocked" or review.get("execution_validated") is not False
            or review.get("scoreable") is not False or type(review.get("valid_full_mission_plan_count")) is not int
            or review["valid_full_mission_plan_count"] != 0
            or review.get("public_contract_validated") is not True):
        raise ValueError("city authoring review must remain blocked, unexecuted and unscoreable")
    blockers = _strings(review.get("blockers"), "authoring blockers")
    if (navigation.get("coordinate_frame") != "city_world"
            or navigation.get("ground_map") != "release/maps/occupancy.yaml"
            or navigation.get("aerial_static_bounds") != "release/maps/semantic_map.json:static_obstacles"
            or navigation.get("navigation_ready") is not False):
        raise ValueError("navigation handoff must bind packaged candidate maps without readiness claims")
    starts = navigation.get("start_reviews")
    if (not isinstance(starts, list) or any(not isinstance(row, dict) for row in starts)
            or len(starts) != len(public["candidate_robot_pool"])
            or {row.get("agent_id") for row in starts} != {row["id"] for row in public["candidate_robot_pool"]}
            or any(row.get("physical_spawn_validated") is not False for row in starts)):
        raise ValueError("navigation start reviews must exactly cover the candidate pool without physical claims")
    maps = _maps(blobs, public, manifest)
    source = _source_binding(private, verify_sources)
    private_tokens = _private_tokens(private, source)
    for label, payload in (("public task", public), ("participant API", api), ("public maps", maps)):
        _public_boundary(payload, private_tokens, label)
    services = _service_review(private, public, manifest)
    return {
        "schema": "eai.benchmark.city_goal_candidate", "version": 1, "kind": _KIND,
        "visibility": "author_only", "scene_key": "city", "task_id": public["task_id"],
        "package_dir": str(root), "public_task": public, "participant_api": api,
        "public_maps": maps, "authoring_review": review, "private_authoring": private,
        "navigation_handoff": navigation, "manifest": manifest, "source_binding": source,
        "service_review": services, "factor_candidate_inputs": _factor_inputs(public, private, manifest),
        "intake_review": {
            "artifact_integrity_validated": True, "shared_contract_validated": True,
            "manifest_file_sha256": hashlib.sha256(blobs["integration_manifest.json"]).hexdigest(),
            "public_task_sha256": public_hash, "artifact_count": len(hashes),
            "authoring_status": "blocked", "execution_status": "external_pending",
            "scoreable": False, "full_goal_bundle": False,
            "blockers": sorted(set(blockers) | {f"missing_shared_service:{name}" for name in services["missing_service_ids"]}),
        },
    }


def validate_city_goal_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only audit: reread files rather than trust cached validation flags.

    The source preparation directory must still exist. If source verification
    was requested by the loader it is repeated. Any in-memory alteration or
    change on disk makes the candidate stale and requires a fresh load.
    """
    if not isinstance(candidate, Mapping) or candidate.get("kind") != _KIND:
        raise ValueError("expected a city preparation candidate")
    if not isinstance(candidate.get("package_dir"), str):
        raise ValueError("city candidate must retain its author-only package directory")
    source = _object(candidate.get("source_binding"), "candidate source_binding")
    refreshed = load_city_goal_candidate(candidate["package_dir"], verify_sources=source.get("verification_requested"))
    if dict(candidate) != refreshed:
        raise ValueError("city candidate is stale or differs from the verified preparation artifacts")
    return {"validated": True, "kind": _KIND, "scene_key": "city", "task_id": refreshed["task_id"],
            "sha256": canonical_sha256(refreshed), "authoring_status": "blocked",
            "execution_status": "external_pending", "scoreable": False,
            "missing_service_ids": list(refreshed["service_review"]["missing_service_ids"])}


__all__ = ["load_city_goal_candidate", "validate_city_goal_candidate"]
