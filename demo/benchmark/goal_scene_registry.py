"""Task-generation registrations; this is not the simulator scene registry."""

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
        "package_kind": "preparation_draft_not_goal_bundle",
        "authoring_entrypoint": "demo.city_benchmark.prepare",
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
                                  verify_sources=False):
    """Compile a reviewed bundle or load a separately labelled blocked draft.

    Registration never activates simulator skills, changes a scene selection,
    or makes a city preparation draft into a four-layer goal bundle.
    """
    registration = goal_scene_registration(scene_key)
    if registration["package_kind"] == "goal_bundle":
        from .goal_driven_tasks import build_goal_task_package
        return {"registration": registration, "bundle": build_goal_task_package(
            scene_key, factor_id=factor_id, factor_level=factor_level)}
    if city_package_dir is None:
        raise ValueError("city requires a prepared package directory from demo.city_benchmark.prepare")
    if factor_id != "communication_quality" or factor_level not in (None, "immediate"):
        raise ValueError("city factor variants require concrete reviewed hidden treatments before admission")
    from .goal_city_intake import load_city_goal_candidate
    return {"registration": registration, "candidate": load_city_goal_candidate(
        city_package_dir, verify_sources=verify_sources)}
