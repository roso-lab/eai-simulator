#!/usr/bin/env python3
"""Load and validate product-adapter profiles without importing ROS."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import re
from typing import Any, Mapping

import yaml


_COMPONENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _merge(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(left))
    for key, value in right.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _component(value: str, field: str) -> str:
    value = str(value).strip().strip("/")
    if not _COMPONENT_RE.fullmatch(value):
        raise ValueError(f"{field} must be one ROS name component, got {value!r}")
    return value


def _vector(value: Any, length: int, field: str) -> list[float]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a {length}-element sequence")
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain numeric values") from exc
    if len(result) != length or not all(math.isfinite(item) for item in result):
        raise ValueError(f"{field} must contain {length} finite values")
    return result


def _positive(value: Any, field: str, *, allow_zero: bool = False) -> float:
    result = float(value)
    if not math.isfinite(result) or (result < 0.0 if allow_zero else result <= 0.0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field} must be {qualifier}")
    return result


def normalize_node_names(value: Any, field: str) -> list[str]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a sequence of fully qualified ROS node names")
    try:
        values = list(value)
    except TypeError as exc:
        raise ValueError(f"{field} must be a sequence of fully qualified ROS node names") from exc
    result: set[str] = set()
    for raw_name in values:
        name = str(raw_name).strip()
        components = [component for component in name.split("/") if component]
        if not name.startswith("/") or not components or not all(
            _COMPONENT_RE.fullmatch(component) for component in components
        ):
            raise ValueError(f"{field} contains invalid fully qualified node name {name!r}")
        result.add("/" + "/".join(components))
    return sorted(result)


def load_profile(
    path: str | Path,
    profile_name: str,
    *,
    robot_name: str,
    nav_hub_id: str,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    path = Path(path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping) or document.get("version") != 1:
        raise ValueError(f"{path} must be a version 1 adapter profile document")
    defaults = document.get("defaults")
    profiles = document.get("profiles")
    if not isinstance(defaults, Mapping) or not isinstance(profiles, Mapping):
        raise ValueError(f"{path} must contain defaults and profiles mappings")
    selected = profiles.get(profile_name)
    if not isinstance(selected, Mapping):
        choices = ", ".join(sorted(str(name) for name in profiles))
        raise ValueError(f"unknown adapter profile {profile_name!r}; choose one of: {choices}")

    robot_name = _component(robot_name, "robot_name")
    nav_hub_id = _component(nav_hub_id, "nav_hub_id")
    config = _merge(defaults, selected)
    topics = config.get("topics")
    frames = config.get("frames")
    input_frames = config.get("input_frames")
    extrinsic = config.get("base_to_lidar")
    timing = config.get("timing")
    clock = config.get("clock")
    duplicate_publishers = config.get("duplicate_publishers")
    mappings = (topics, frames, input_frames, extrinsic, timing, clock, duplicate_publishers)
    if not all(isinstance(value, Mapping) for value in mappings):
        raise ValueError(
            "profile topics, frames, input_frames, base_to_lidar, timing, clock, and "
            "duplicate_publishers must be mappings"
        )

    variables = {"robot_name": robot_name, "nav_hub_id": nav_hub_id}

    def render(value: Any, field: str) -> str:
        try:
            rendered = str(value).format_map(variables)
        except KeyError as exc:
            raise ValueError(f"unsupported placeholder in {field}: {exc.args[0]}") from exc
        if not rendered.startswith("/"):
            raise ValueError(f"{field} must resolve to an absolute ROS topic")
        return rendered

    def render_frame(value: Any, field: str) -> str:
        try:
            rendered = str(value).format_map(variables).strip()
        except KeyError as exc:
            raise ValueError(f"unsupported placeholder in {field}: {exc.args[0]}") from exc
        components = rendered.split("/")
        if not components or not all(_COMPONENT_RE.fullmatch(item) for item in components):
            raise ValueError(f"{field} must be a relative ROS frame id, got {rendered!r}")
        return rendered

    topic_keys = (
        "cloud_input",
        "odometry_input",
        "cmd_vel_input",
        "cloud_output",
        "odometry_output",
        "cmd_vel_output",
    )
    resolved_topics = {key: render(topics[key], f"topics.{key}") for key in topic_keys}
    for key, value in (overrides or {}).items():
        if key in {"cloud_input", "odometry_input"} and str(value).strip():
            resolved_topics[key] = render(str(value).strip(), f"override {key}")
    contract_topics = {
        "cmd_vel_input": f"/{nav_hub_id}/cmd_vel",
        "cloud_output": f"/{nav_hub_id}/tower/mapping/cloud",
        "odometry_output": f"/{nav_hub_id}/tower/mapping/odometry",
        "cmd_vel_output": f"/{robot_name}/cmd_vel",
    }
    for key, expected in contract_topics.items():
        if resolved_topics[key] != expected:
            raise ValueError(f"topics.{key} must preserve the product contract {expected!r}")
    if len(set(resolved_topics.values())) != len(resolved_topics):
        raise ValueError("adapter input and output topics must all be distinct")

    mapping_frame = str(frames["mapping_init"]).format_map(variables).strip().strip("/")
    lidar_frame = str(frames["lidar"]).format_map(variables).strip().strip("/")
    if mapping_frame != f"{nav_hub_id}/mapping_init":
        raise ValueError("frames.mapping_init must remain <nav_hub_id>/mapping_init")
    if lidar_frame != f"{nav_hub_id}/lidar_link":
        raise ValueError("frames.lidar must remain <nav_hub_id>/lidar_link")
    cloud_input_frame = render_frame(input_frames["cloud"], "input_frames.cloud")
    odometry_input_frame = render_frame(
        input_frames["odometry"], "input_frames.odometry"
    )
    odometry_child_frame = render_frame(
        input_frames["base_footprint"], "input_frames.base_footprint"
    )
    if clock.get("owner") != "external" or clock.get("adapter_publishes") is not False:
        raise ValueError("adapter profiles must declare an external /clock owner and no adapter publisher")
    clock_topic = render(clock.get("topic"), "clock.topic")
    duplicate_publisher_action = str(duplicate_publishers.get("action", "fail")).lower()
    if duplicate_publisher_action not in {"fail", "warn"}:
        raise ValueError("duplicate_publishers.action must be 'fail' or 'warn'")
    passive_product_nodes = normalize_node_names(
        duplicate_publishers.get("passive_product_nodes", ()),
        "duplicate_publishers.passive_product_nodes",
    )

    result: dict[str, Any] = {
        "profile_name": str(profile_name),
        "robot_name": robot_name,
        "nav_hub_id": nav_hub_id,
        **{f"{key}_topic": value for key, value in resolved_topics.items()},
        "cloud_input_frame": cloud_input_frame,
        "odometry_input_frame": odometry_input_frame,
        "odometry_child_frame": odometry_child_frame,
        "mapping_frame": mapping_frame,
        "lidar_frame": lidar_frame,
        "base_to_lidar_xyz": _vector(extrinsic["translation_xyz_m"], 3, "base_to_lidar.translation_xyz_m"),
        "base_to_lidar_rpy": _vector(extrinsic["rotation_rpy_rad"], 3, "base_to_lidar.rotation_rpy_rad"),
        "expected_cloud_rate_hz": _positive(timing["expected_cloud_rate_hz"], "timing.expected_cloud_rate_hz"),
        "product_odometry_rate_hz": _positive(
            timing["product_odometry_rate_hz"], "timing.product_odometry_rate_hz"
        ),
        "odom_buffer_sec": _positive(timing["odom_buffer_sec"], "timing.odom_buffer_sec"),
        "max_interpolation_gap_sec": _positive(
            timing["max_interpolation_gap_sec"], "timing.max_interpolation_gap_sec"
        ),
        "backward_jump_threshold_sec": _positive(
            timing["backward_jump_threshold_sec"],
            "timing.backward_jump_threshold_sec",
            allow_zero=True,
        ),
        "max_pending_clouds": int(timing["max_pending_clouds"]),
        "intensity_field": str(config.get("intensity_field", "intensity")),
        "default_intensity": float(config.get("default_intensity", 0.0)),
        "clock_owner": "external",
        "clock_topic": clock_topic,
        "duplicate_publisher_action": duplicate_publisher_action,
        "passive_product_publisher_allowlist": passive_product_nodes,
        "publisher_guard_period_sec": _positive(
            duplicate_publishers.get("guard_period_sec", 2.0),
            "duplicate_publishers.guard_period_sec",
        ),
    }
    if result["max_pending_clouds"] <= 0:
        raise ValueError("timing.max_pending_clouds must be positive")
    if not math.isfinite(result["default_intensity"]):
        raise ValueError("default_intensity must be finite")
    return result
