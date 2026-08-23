from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import Catalog, DeviceSpec, InterfaceSpec


class CatalogError(ValueError):
    pass


DEFAULT_CATALOG_ROOT = Path(__file__).with_name("interfaces")


_DEVICE_FIELDS = ("id", "name", "category", "models", "interfaces")
_INTERFACE_FIELDS = ("id", "name", "protocol", "direction", "kind", "endpoint", "data_type")


def _required(mapping: dict[str, Any], fields: tuple[str, ...], path: Path) -> None:
    for field in fields:
        if field not in mapping or mapping[field] in (None, ""):
            raise CatalogError(f"{path}: missing required field '{field}'")


def _attachment_gate(raw: Any, *, field: str, path: Path) -> tuple[str, ...]:
    if raw is None:
        return ()
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list) or not values or not all(
        isinstance(value, str) and value.strip() for value in values
    ):
        raise CatalogError(f"{path}: {field} must be a string or a non-empty list of strings")
    return tuple(value.strip().casefold() for value in values)


def _load_device(path: Path) -> DeviceSpec:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CatalogError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(payload, dict):
        raise CatalogError(f"{path}: manifest must contain a mapping")
    _required(payload, _DEVICE_FIELDS, path)
    if not isinstance(payload["models"], list) or not payload["models"]:
        raise CatalogError(f"{path}: models must be a non-empty list")
    if not isinstance(payload["interfaces"], list):
        raise CatalogError(f"{path}: interfaces must be a list")

    interfaces = []
    for index, raw in enumerate(payload["interfaces"]):
        if not isinstance(raw, dict):
            raise CatalogError(f"{path}: interfaces[{index}] must be a mapping")
        _required(raw, _INTERFACE_FIELDS, path)
        known = set(_INTERFACE_FIELDS) | {
            "description",
            "example",
            "read_only_test",
            "requires_attachment",
            "excludes_attachment",
        }
        interfaces.append(
            InterfaceSpec(
                id=str(raw["id"]),
                name=str(raw["name"]),
                protocol=str(raw["protocol"]),
                direction=str(raw["direction"]),
                kind=str(raw["kind"]),
                endpoint=str(raw["endpoint"]),
                data_type=str(raw["data_type"]),
                description=str(raw.get("description", "")),
                example=str(raw.get("example", "")),
                read_only_test=raw.get("read_only_test"),
                requires_attachments=_attachment_gate(
                    raw.get("requires_attachment"), field="requires_attachment", path=path
                ),
                excludes_attachments=_attachment_gate(
                    raw.get("excludes_attachment"), field="excludes_attachment", path=path
                ),
                metadata={key: value for key, value in raw.items() if key not in known},
            )
        )
    known_device = set(_DEVICE_FIELDS) | {"description"}
    return DeviceSpec(
        id=str(payload["id"]),
        name=str(payload["name"]),
        category=str(payload["category"]),
        models=tuple(str(model) for model in payload["models"]),
        description=str(payload.get("description", "")),
        interfaces=tuple(interfaces),
        metadata={key: value for key, value in payload.items() if key not in known_device},
    )


def load_catalog(root: Path | str | None = None) -> Catalog:
    root_path = DEFAULT_CATALOG_ROOT if root is None else Path(root)
    if not root_path.is_dir():
        raise CatalogError(f"Catalog directory does not exist: {root_path}")
    devices = tuple(_load_device(path) for path in sorted(root_path.rglob("*.yaml")))
    seen_devices: set[str] = set()
    seen_interfaces: set[str] = set()
    for device in devices:
        if device.id in seen_devices:
            raise CatalogError(f"Duplicate device id: {device.id}")
        seen_devices.add(device.id)
        for interface in device.interfaces:
            if interface.id in seen_interfaces:
                raise CatalogError(f"Duplicate interface id: {interface.id}")
            seen_interfaces.add(interface.id)
    return Catalog(devices=devices)
