from __future__ import annotations

from typing import Any

from .models import Catalog, CatalogEntry, ResolvedInterface


def query_interfaces(
    catalog: Catalog,
    *,
    robot: str | None = None,
    sensor: str | None = None,
    protocol: str | None = None,
    data_type: str | None = None,
    text: str | None = None,
) -> list[CatalogEntry]:
    results: list[CatalogEntry] = []
    for device in catalog.devices:
        if robot and (device.category != "robot" or not device.matches_model(robot)):
            continue
        if sensor and (device.category != "sensor" or not device.matches_model(sensor)):
            continue
        for interface in device.interfaces:
            if protocol and interface.protocol.casefold() != protocol.casefold():
                continue
            if data_type and data_type.casefold() not in interface.data_type.casefold():
                continue
            haystack = " ".join(
                (device.id, device.name, device.description, interface.id, interface.name, interface.description, interface.data_type)
            ).casefold()
            if text and text.casefold() not in haystack:
                continue
            results.append(CatalogEntry(device=device, interface=interface))
    return results


def _format_template(value: str, context: dict[str, Any]) -> str:
    rendered = value
    for key, replacement in context.items():
        rendered = rendered.replace("{" + key + "}", str(replacement))
    return rendered


def resolve_scene_interfaces(
    catalog: Catalog,
    selection: dict[str, Any] | None,
    *,
    env_name: str,
    possible_agents: list[str] | None = None,
) -> list[ResolvedInterface]:
    robots = list((selection or {}).get("robots", []))
    if not robots and possible_agents:
        robots = [{"type": name.rsplit("_", 1)[0], "instance_name": name, "attachments": []} for name in possible_agents]

    resolved: list[ResolvedInterface] = []
    type_counts: dict[str, int] = {}
    for index, robot in enumerate(robots, start=1):
        robot_type = str(robot.get("type", "robot"))
        normalized_type = robot_type.casefold()
        type_counts[normalized_type] = type_counts.get(normalized_type, 0) + 1
        runtime_name = possible_agents[index - 1] if possible_agents and index <= len(possible_agents) else None
        instance_name = str(
            runtime_name
            or robot.get("instance_name")
            or f"{robot_type}_{type_counts[normalized_type]}"
        )
        attachments = [str(item.get("type")) for item in robot.get("attachments", [])]
        normalized_attachments = {attachment.casefold() for attachment in attachments}
        targets = [(device, None) for device in catalog.devices if device.category == "robot" and device.matches_model(robot_type)]
        targets.extend(
            (device, attachment)
            for attachment in attachments
            for device in catalog.devices
            if device.category in {"sensor", "tool"} and device.matches_model(attachment)
        )
        for device, attachment in targets:
            context = {
                "robot": instance_name,
                "robot_type": robot_type,
                "sensor": attachment or "",
                "env": env_name,
                "index": index,
            }
            for interface in device.interfaces:
                if interface.requires_attachments and not all(
                    attachment in normalized_attachments for attachment in interface.requires_attachments
                ):
                    continue
                if any(attachment in normalized_attachments for attachment in interface.excludes_attachments):
                    continue
                resolved.append(
                    ResolvedInterface(
                        device_id=device.id,
                        device_name=device.name,
                        category=device.category,
                        interface_id=interface.id,
                        interface_name=interface.name,
                        protocol=interface.protocol,
                        direction=interface.direction,
                        kind=interface.kind,
                        endpoint=_format_template(interface.endpoint, context),
                        data_type=interface.data_type,
                        description=interface.description,
                        example=_format_template(interface.example, context),
                        instance_name=instance_name,
                        robot_type=robot_type,
                        attachment=attachment,
                    )
                )
    return resolved
