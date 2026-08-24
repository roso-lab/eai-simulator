from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InterfaceSpec:
    id: str
    name: str
    protocol: str
    direction: str
    kind: str
    endpoint: str
    data_type: str
    description: str = ""
    example: str = ""
    read_only_test: dict[str, Any] | None = None
    requires_attachments: tuple[str, ...] = ()
    excludes_attachments: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_read_only(self) -> bool:
        return self.direction.lower() in {"output", "read", "read_only"}


@dataclass(frozen=True)
class DeviceSpec:
    id: str
    name: str
    category: str
    models: tuple[str, ...]
    description: str
    interfaces: tuple[InterfaceSpec, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def matches_model(self, value: str) -> bool:
        normalized = value.casefold()
        return any(model.casefold() == normalized for model in self.models)


@dataclass(frozen=True)
class Catalog:
    devices: tuple[DeviceSpec, ...]

    def interface(self, interface_id: str) -> InterfaceSpec:
        for device in self.devices:
            for interface in device.interfaces:
                if interface.id == interface_id:
                    return interface
        raise KeyError(interface_id)

    def device_for_interface(self, interface_id: str) -> DeviceSpec:
        for device in self.devices:
            if any(interface.id == interface_id for interface in device.interfaces):
                return device
        raise KeyError(interface_id)


@dataclass(frozen=True)
class CatalogEntry:
    device: DeviceSpec
    interface: InterfaceSpec


@dataclass(frozen=True)
class ResolvedInterface:
    device_id: str
    device_name: str
    category: str
    interface_id: str
    interface_name: str
    protocol: str
    direction: str
    kind: str
    endpoint: str
    data_type: str
    description: str
    example: str
    instance_name: str
    robot_type: str
    attachment: str | None = None
    state: str = "declared"

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "category": self.category,
            "id": self.interface_id,
            "name": self.interface_name,
            "protocol": self.protocol,
            "direction": self.direction,
            "kind": self.kind,
            "endpoint": self.endpoint,
            "data_type": self.data_type,
            "description": self.description,
            "example": self.example,
            "instance_name": self.instance_name,
            "robot_type": self.robot_type,
            "attachment": self.attachment,
            "state": self.state,
        }
