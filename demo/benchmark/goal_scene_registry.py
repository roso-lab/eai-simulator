"""Task-generation registrations; this is not the simulator scene registry.

The catalog includes authoring-only scene families so task-generation tooling
has one vocabulary.  ``package_kind`` remains the admission boundary: only
``goal_bundle`` entries enter the formal factor matrix.
"""

from copy import deepcopy


_SCENES = {
    "factory": {
        "business_family": "production_anomaly_confirmation_and_maintenance_dispatch",
        "package_kind": "goal_bundle", "authoring_entrypoint": "demo.benchmark.goal_driven_tasks",
    },
    "warehouse_nvd": {
        "business_family": "order_fulfilment_inventory_and_dock_closeout",
        "package_kind": "goal_bundle", "authoring_entrypoint": "demo.benchmark.goal_driven_tasks",
    },
    "modern_house_nvd": {
        "business_family": "resident_home_recovery_and_access_restoration",
        "package_kind": "goal_bundle", "authoring_entrypoint": "demo.benchmark.goal_driven_tasks",
    },
    "city": {
        "business_family": "urban_incident_response",
        "package_kind": "goal_bundle",
        "authoring_entrypoint": "demo.benchmark.goal_driven_tasks",
        "scene_preparation_entrypoint": "demo.city_benchmark.prepare",
    },
    "restaurant": {
        "business_family": "restaurant_opening_readiness_inspection",
        "package_kind": "authoring_only",
        "authoring_entrypoint": "demo.benchmark.goal_driven_tasks",
        "authoring_status": "passed",
        "execution_status": "external_pending",
        "scoreable": False,
    },
}


def registered_goal_scenes():
    return [{"scene_key": key, **deepcopy(value)} for key, value in _SCENES.items()]


def goal_scene_registration(scene_key):
    if not isinstance(scene_key, str) or scene_key not in _SCENES:
        raise ValueError(f"unknown goal scene: {scene_key!r}")
    return {"scene_key": scene_key, **deepcopy(_SCENES[scene_key])}


def compile_registered_goal_scene(scene_key, *, city_package_dir=None,
                                  factor_id="communication_quality", factor_level=None,
                                  verify_sources=False, mechanism_family=None):
    """Compile the same reviewed goal contract for every registered scene.

    Registration never activates simulator skills or changes a scene selection.
    An authoring-only entry can be compiled for contract review, but its
    release gate remains owned by the scene's evidence admission.
    """
    registration = goal_scene_registration(scene_key)
    from .goal_driven_tasks import build_goal_task_package
    result = {"registration": registration, "bundle": build_goal_task_package(
        scene_key, factor_id=factor_id, factor_level=factor_level,
        mechanism_family=mechanism_family)}
    gate = result["bundle"]["hidden_evaluator"]["release_gate"]
    if registration["package_kind"] == "authoring_only":
        if (gate.get("authoring_status") != registration["authoring_status"]
                or gate.get("execution_status") != registration["execution_status"]
                or gate.get("scoreable") is not registration["scoreable"]):
            raise ValueError("authoring-only scene release gate disagrees with its registration")
    if city_package_dir is not None:
        if scene_key != "city":
            raise ValueError("city preparation is only applicable to the city scene")
        from .goal_city_intake import load_city_goal_candidate
        result["candidate"] = load_city_goal_candidate(city_package_dir, verify_sources=verify_sources)
    return result
