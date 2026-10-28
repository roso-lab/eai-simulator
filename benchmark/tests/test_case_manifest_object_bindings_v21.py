from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from benchmark import case_manifest_builder_v1 as builder
from benchmark.case_manifest_builder_v1 import CaseManifestBuildError, build_case_manifest
from benchmark.case_manifest_v1 import (
    LOGICAL_TASK_OBJECT_BINDINGS_SCHEMA,
    OBJECT_BINDINGS_SCHEMA,
    TASK_OBJECT_BINDINGS_SCHEMA,
    CaseManifestError,
    bind_case_manifest,
    validate_case_manifest,
)


SCENE_DIGEST = "1" * 64
OTHER_DIGEST = "2" * 64


def _generated_bindings():
    return {"schema": TASK_OBJECT_BINDINGS_SCHEMA, "scene_digest": SCENE_DIGEST,
            "objects": [{"entity_id": entity, "origin": "task_generated",
                         "definition_ref": "task-setup:room06-v3/" + entity,
                         "definition_digest": "e" * 64}
                        for entity in ("book-batch", "heavy-object")]}


def _logical_bindings():
    return {
        "schema": LOGICAL_TASK_OBJECT_BINDINGS_SCHEMA,
        "scene_digest": SCENE_DIGEST,
        "objects": [
            {
                "entity_id": "book-batch",
                "category": "book",
                "metadata": {
                    "mass_kg": 0.2,
                    "batch_id": "book-batch-01",
                    "allowed_receivers": ["shelf-1", "shelf-2", "shelf-3"],
                },
                "initial_semantic_point": "floor1-bookshelf",
                "target_semantic_point": "floor2-bookshelf-1",
            },
            {
                "entity_id": "heavy-object",
                "category": "heavy-object",
                "metadata": {"mass_kg": 2.2, "minimum_holders": 2},
                "initial_semantic_point": "floor1-heavy-object",
                "target_semantic_point": "floor2-heavy-object-target",
            },
        ],
    }


def test_logical_task_objects_need_no_prim_confirmation_or_readback():
    scene = _scene(object_bindings=False)
    scene["object_bindings"] = _logical_bindings()

    result = bind_case_manifest(_manifest(scene))

    assert result["scene"]["object_bindings"] == _logical_bindings()
    assert builder._object_bindings(
        _logical_bindings(),
        scene_digest=SCENE_DIGEST,
        entity_ids=scene["entity_ids"],
    ) == _logical_bindings()


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda value: value["objects"][0].update(prim_path="/World/Book"),
            "schema mismatch",
        ),
        (
            lambda value: value["objects"][0]["metadata"].clear(),
            None,
        ),
        (
            lambda value: value["objects"][0]["metadata"].update(mass_kg=0.0),
            "positive finite number",
        ),
        (
            lambda value: value["objects"][0]["metadata"].update(mass_kg=float("nan")),
            "positive finite number",
        ),
        (
            lambda value: value["objects"][0]["metadata"].update(mass_kg=True),
            "positive finite number",
        ),
        (
            lambda value: value["objects"][1]["metadata"].update(minimum_holders=0),
            "minimum_holders must be a positive integer",
        ),
        (
            lambda value: value["objects"][1]["metadata"].update(minimum_holders=True),
            "minimum_holders must be a positive integer",
        ),
        (
            lambda value: value["objects"][0]["metadata"].update(prim_path="/World/Book"),
            "physical dependency key",
        ),
        (
            lambda value: value["objects"][0]["metadata"].update(
                skill={"attachment_ref": "joint-1"}
            ),
            "physical dependency key",
        ),
        (
            lambda value: value["objects"][0]["metadata"].update(
                runtime={"primPath": "/World/Book"}
            ),
            "physical dependency key",
        ),
        (
            lambda value: value["objects"][0]["metadata"].update(
                size_xyz_m=[0.1, 0.2, 0.3]
            ),
            "physical dependency key",
        ),
        (
            lambda value: value["objects"][0]["metadata"].update(
                render={"mesh-path": "book.obj"}
            ),
            "physical dependency key",
        ),
        (
            lambda value: value["objects"][0].update(category=""),
            "category must be non-empty text",
        ),
        (
            lambda value: value["objects"].pop(),
            "exactly cover entity_ids",
        ),
        (
            lambda value: value["objects"][1].update(entity_id="book-batch"),
            "without duplicates",
        ),
    ],
)
def test_logical_task_object_bindings_reject_invalid_rows(mutate, message):
    scene = _scene(object_bindings=False)
    value = _logical_bindings()
    mutate(value)
    scene["object_bindings"] = value

    if message is None:
        assert bind_case_manifest(_manifest(scene))["scene"]["object_bindings"] == value
    else:
        with pytest.raises(CaseManifestError, match=message):
            bind_case_manifest(_manifest(scene))


def test_logical_task_object_rows_are_bound_into_manifest_digest():
    scene = _scene(object_bindings=False)
    scene["object_bindings"] = _logical_bindings()
    result = bind_case_manifest(_manifest(scene))
    result["scene"]["object_bindings"]["objects"][0]["metadata"]["mass_kg"] = 0.3

    with pytest.raises(CaseManifestError, match="manifest_digest"):
        validate_case_manifest(result)


def test_task_generated_identity_needs_no_prim_or_human_confirmation():
    scene = _scene(object_bindings=False)
    scene['object_bindings'] = _generated_bindings()
    result = bind_case_manifest(_manifest(scene))
    assert result['scene']['object_bindings'] == _generated_bindings()
    assert builder._object_bindings(_generated_bindings(), scene_digest=SCENE_DIGEST,
                                   entity_ids=scene['entity_ids']) == _generated_bindings()


@pytest.mark.parametrize('mutation', ['missing', 'digest', 'prop', 'duplicate', 'prim'])
def test_generated_identity_rejects_incomplete_or_ambiguous_setup(mutation):
    scene = _scene(object_bindings=False)
    value = _generated_bindings()
    if mutation == 'missing':
        value['objects'].pop()
    elif mutation == 'digest':
        value['objects'][0]['definition_digest'] = ''
    elif mutation == 'prop':
        value['objects'][0]['origin'] = 'scene_prop'
    elif mutation == 'duplicate':
        value['objects'][1]['entity_id'] = value['objects'][0]['entity_id']
    else:
        value['objects'][0]['prim_path'] = '/World/NotConfirmed'
    scene['object_bindings'] = value
    with pytest.raises(CaseManifestError):
        bind_case_manifest(_manifest(scene))


def test_task_definition_is_bound_into_manifest_digest():
    scene = _scene(object_bindings=False)
    scene['object_bindings'] = _generated_bindings()
    result = bind_case_manifest(_manifest(scene))
    result['scene']['object_bindings']['objects'][0]['definition_ref'] = 'different-task'
    with pytest.raises(CaseManifestError, match='manifest_digest'):
        validate_case_manifest(result)


def _object_bindings() -> dict:
    return {
        "schema": OBJECT_BINDINGS_SCHEMA,
        "scene_digest": SCENE_DIGEST,
        "confirmation": {
            "status": "confirmed",
            "confirmed_by": "benchmark author",
            "confirmed_at": "2026-10-26T12:00:00+08:00",
            "source": "room_06 case binding review",
        },
        "objects": [
            {"entity_id": "book-batch", "prim_path": "/World/Books"},
            {"entity_id": "heavy-object", "prim_path": "/World/HeavyObject"},
        ],
    }


def _scene(*, object_bindings: bool) -> dict:
    value = {
        "scene_id": "room_06",
        "scene_digest": SCENE_DIGEST,
        "semantic_map_digest": "3" * 64,
        "topology_digest": "4" * 64,
        "frame": "world",
        "units": "m",
        "entity_ids": ["book-batch", "heavy-object"],
        "zone_ids": [],
        "horizon": 15000,
        "tau_stable": 25,
    }
    if object_bindings:
        value["object_bindings"] = _object_bindings()
    return value


def _manifest(scene: dict) -> dict:
    return {
        "schema": "heterocoord.case-manifest.v1",
        "case_id": "room06-livt04-r1",
        "task_id": "liv-t-04",
        "anchor_id": "liv-t-04",
        "domain": "living_public",
        "relation_signature": ["P2", "P3-J"],
        "base_instance_id": "room06-livt04",
        "scene": scene,
        "robot_pool": {
            "pool_id": "pool-v3.1",
            "roster_digest": "5" * 64,
            "robots": [{
                "robot_id": "robot-01",
                "asset_digest": "6" * 64,
                "capability_digest": "7" * 64,
                "initial_state_digest": "8" * 64,
            }],
        },
        "contract": {
            "contract_digest": "9" * 64,
            "oracle_id": "liv-t-04-oracle",
            "contract_revision": "2026-10-26",
        },
        "catalog_digest": "a" * 64,
        "plan_digest": "b" * 64,
        "replay": {
            "serializer_version": "v1",
            "rng_stream_key": "room06-r1",
            "checkpoint_schema": "v1",
        },
        "evaluator": {"evaluator_id": "oracle-v1", "evaluator_version": "v1"},
    }


def _write_semantic_bundle(root: Path, *, entities: list[str]) -> None:
    footprints = {
        "scene_id": "room_06",
        "coordinate_frame": "world",
        "semantics": {
            "entities": [{"id": entity_id} for entity_id in entities],
            "regions": [],
        },
    }
    topology = {"scene_key": "room_06", "coordinate_frame": "world"}
    footprint_bytes = json.dumps(footprints, sort_keys=True).encode()
    topology_bytes = json.dumps(topology, sort_keys=True).encode()
    (root / "semantic-footprints.json").write_bytes(footprint_bytes)
    (root / "topology.json").write_bytes(topology_bytes)
    manifest = {
        "scene_key": "room_06",
        "files": {
            "semantic-footprints.json": hashlib.sha256(footprint_bytes).hexdigest(),
            "topology.json": hashlib.sha256(topology_bytes).hexdigest(),
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _binding() -> dict:
    return {
        "format_contract_version": "2.1",
        "case_id": "room06-livt04-r1",
        "task_id": "liv-t-04",
        "anchor_id": "liv-t-04",
        "domain": "living_public",
        "relation_signature": ["P2", "P3-J"],
        "base_instance_id": "room06-livt04",
        "scene_digest": SCENE_DIGEST,
        "entity_ids": ["book-batch", "heavy-object"],
        "zone_ids": [],
        "horizon": 15000,
        "tau_stable": 25,
        "units": "m",
        "object_bindings": _object_bindings(),
        "semantic_requirements": {
            "semantic_points": [
                "floor1-bookshelf", "floor2-bookshelf-1",
                "floor2-bookshelf-2", "floor2-bookshelf-3",
            ]
        },
        "pool_id": "pool-v3.1",
        "resume_ids": ["robot-01"],
        "contract": {
            "contract_digest": "9" * 64,
            "oracle_id": "liv-t-04-oracle",
            "contract_revision": "2026-10-26",
        },
        "catalog_digest": "a" * 64,
        "plan_digest": "b" * 64,
        "replay": {
            "serializer_version": "v1",
            "rng_stream_key": "room06-r1",
            "checkpoint_schema": "v1",
        },
        "evaluator": {"evaluator_id": "oracle-v1", "evaluator_version": "v1"},
    }


def _patch_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        builder,
        "audit_case_inputs",
        lambda *args, **kwargs: {"case_ready": True, "failures": []},
    )
    monkeypatch.setattr(
        builder,
        "_runtime_robots",
        lambda *args, **kwargs: (
            "pool-v3.1",
            [{
                "robot_id": "robot-01",
                "asset_digest": "6" * 64,
                "capability_digest": "7" * 64,
                "initial_state_digest": "8" * 64,
            }],
        ),
    )


def test_v21_builder_accepts_case_owned_objects_and_no_zones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_semantic_bundle(tmp_path, entities=[])
    pool = tmp_path / "pool.json"
    pool.write_text("{}", encoding="utf-8")
    _patch_upstream(monkeypatch)

    result = build_case_manifest(
        semantic_root=tmp_path,
        robot_pool_path=pool,
        binding=_binding(),
        physical_object_experiment_authorization="fixture-physical-experiment",
    )

    assert result["scene"]["zone_ids"] == []
    assert result["scene"]["object_bindings"] == _object_bindings()
    assert validate_case_manifest(result) == result


def test_v21_builder_rejects_physical_object_bindings_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_semantic_bundle(tmp_path, entities=[])
    pool = tmp_path / "pool.json"
    pool.write_text("{}", encoding="utf-8")
    _patch_upstream(monkeypatch)

    with pytest.raises(
        CaseManifestBuildError,
        match="physical_object_experiment_authorization",
    ):
        build_case_manifest(
            semantic_root=tmp_path, robot_pool_path=pool, binding=_binding()
        )


def test_v21_builder_accepts_logical_objects_without_prim_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_semantic_bundle(tmp_path, entities=[])
    pool = tmp_path / "pool.json"
    pool.write_text("{}", encoding="utf-8")
    _patch_upstream(monkeypatch)
    binding = _binding()
    binding["object_bindings"] = _logical_bindings()

    result = build_case_manifest(
        semantic_root=tmp_path, robot_pool_path=pool, binding=binding
    )

    assert result["scene"]["object_bindings"] == _logical_bindings()
    assert validate_case_manifest(result) == result


def test_v21_builder_fails_closed_without_case_owned_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_semantic_bundle(tmp_path, entities=[])
    pool = tmp_path / "pool.json"
    pool.write_text("{}", encoding="utf-8")
    _patch_upstream(monkeypatch)
    binding = _binding()
    del binding["object_bindings"]

    with pytest.raises(CaseManifestBuildError, match="object_bindings is required"):
        build_case_manifest(semantic_root=tmp_path, robot_pool_path=pool, binding=binding)


def test_legacy_semantic_entity_binding_remains_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_semantic_bundle(tmp_path, entities=["book-batch", "heavy-object"])
    pool = tmp_path / "pool.json"
    pool.write_text("{}", encoding="utf-8")
    _patch_upstream(monkeypatch)
    binding = _binding()
    del binding["format_contract_version"]
    del binding["object_bindings"]

    result = build_case_manifest(
        semantic_root=tmp_path, robot_pool_path=pool, binding=binding
    )

    assert "object_bindings" not in result["scene"]
    assert validate_case_manifest(result) == result


def test_explicit_v21_requires_object_bindings_even_when_legacy_entities_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_semantic_bundle(tmp_path, entities=["book-batch", "heavy-object"])
    pool = tmp_path / "pool.json"
    pool.write_text("{}", encoding="utf-8")
    _patch_upstream(monkeypatch)
    binding = _binding()
    del binding["object_bindings"]

    with pytest.raises(CaseManifestBuildError, match="object_bindings is required"):
        build_case_manifest(semantic_root=tmp_path, robot_pool_path=pool, binding=binding)


def test_object_binding_confirmation_is_covered_by_manifest_digest() -> None:
    manifest = bind_case_manifest(_manifest(_scene(object_bindings=True)))
    tampered = copy.deepcopy(manifest)
    tampered["scene"]["object_bindings"]["confirmation"]["confirmed_by"] = "attacker"

    with pytest.raises(CaseManifestError, match="manifest_digest"):
        validate_case_manifest(tampered)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda value: value["confirmation"].update(status="pending"),
            "explicitly confirmed",
        ),
        (
            lambda value: value.update(scene_digest=OTHER_DIGEST),
            "does not match binding.scene_digest",
        ),
        (
            lambda value: value["objects"].pop(),
            "exactly cover binding.entity_ids",
        ),
        (
            lambda value: value["objects"][1].update(prim_path="/World/Books"),
            "duplicate prim_path",
        ),
        (
            lambda value: value.update(extra="forged"),
            "schema mismatch",
        ),
    ],
)
def test_builder_rejects_untrusted_object_binding_books(mutate, message: str) -> None:
    value = _object_bindings()
    mutate(value)

    with pytest.raises(CaseManifestBuildError, match=message):
        builder._object_bindings(
            value,
            scene_digest=SCENE_DIGEST,
            entity_ids=["book-batch", "heavy-object"],
            physical_object_experiment_authorization="fixture-physical-experiment",
        )


def test_manifest_rejects_object_bindings_that_do_not_cover_scene_entities() -> None:
    scene = _scene(object_bindings=True)
    scene["object_bindings"]["objects"].pop()

    with pytest.raises(CaseManifestError, match="exactly cover scene.entity_ids"):
        bind_case_manifest(_manifest(scene))


def test_manifest_rejects_extra_object_binding_fields() -> None:
    scene = _scene(object_bindings=True)
    scene["object_bindings"]["unregistered"] = True

    with pytest.raises(CaseManifestError, match="scene.object_bindings schema mismatch"):
        bind_case_manifest(_manifest(scene))


def test_builder_cli_archives_fail_closed_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binding_path = tmp_path / "binding.json"
    binding_path.write_text(json.dumps(_binding()), encoding="utf-8")
    audit_path = tmp_path / "evidence" / "input-audit.json"
    output_path = tmp_path / "case-manifest.json"
    monkeypatch.setattr(
        builder,
        "audit_case_inputs",
        lambda *args, **kwargs: {
            "schema": "heterocoord.case-input-audit.v1",
            "case_ready": False,
            "failures": ["semantic map is still being edited"],
        },
    )

    with pytest.raises(SystemExit) as excinfo:
        builder.main([
            "--semantic-root", str(tmp_path / "semantic_map"),
            "--robot-pool", str(tmp_path / "pool.json"),
            "--binding", str(binding_path),
            "--output", str(output_path),
            "--audit-output", str(audit_path),
        ])

    assert excinfo.value.code == 2
    assert json.loads(audit_path.read_text(encoding="utf-8"))["case_ready"] is False
    assert "semantic map is still being edited" in json.loads(
        audit_path.read_text(encoding="utf-8")
    )["failures"]
    assert not output_path.exists()
