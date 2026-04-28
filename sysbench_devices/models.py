"""Data models shared by the daemon, transports, SDK, and CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


ADMIN_ATTRIBUTION_ID = "admin"


class DeviceState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    RESERVED = "reserved"


class PowerAction(StrEnum):
    ON = "on"
    OFF = "off"
    CYCLE = "cycle"


@dataclass(frozen=True)
class DeviceRegistration:
    id: str
    name: str | None = None
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "tags": list(self.tags)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceRegistration":
        return cls(
            id=str(data["id"]),
            name=None if data.get("name") is None else str(data["name"]),
            tags=tuple(str(tag) for tag in data.get("tags", [])),
        )


@dataclass(frozen=True)
class RuntimeDevice:
    id: str
    serial_port: str | None = None
    power_target: str | None = None
    usb_path: str | None = None
    usb_serial: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "serial_port": self.serial_port,
            "power_target": self.power_target,
            "usb_path": self.usb_path,
            "usb_serial": self.usb_serial,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeDevice":
        return cls(
            id=str(data["id"]),
            serial_port=data.get("serial_port"),
            power_target=data.get("power_target"),
            usb_path=data.get("usb_path"),
            usb_serial=data.get("usb_serial"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ReservationAttribution:
    kind: Literal["api_key", "socket"]
    id: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "label": self.label}

    @classmethod
    def admin(cls) -> "ReservationAttribution":
        return cls(kind="socket", id=ADMIN_ATTRIBUTION_ID, label="Unix socket admin")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReservationAttribution":
        return cls(kind=data["kind"], id=str(data["id"]), label=str(data["label"]))


@dataclass(frozen=True)
class Reservation:
    id: str
    device_id: str
    attribution: ReservationAttribution

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "attribution": self.attribution.to_dict(),
        }


@dataclass(frozen=True)
class Device:
    id: str
    name: str | None
    tags: tuple[str, ...]
    state: DeviceState
    runtime: RuntimeDevice | None = None
    reservation: Reservation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tags": list(self.tags),
            "state": self.state.value,
            "runtime": None if self.runtime is None else self.runtime.to_dict(),
            "reservation": None if self.reservation is None else self.reservation.to_dict(),
        }


@dataclass(frozen=True)
class DiscoveredDevice:
    id: str
    runtime: RuntimeDevice

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "runtime": self.runtime.to_dict()}


@dataclass(frozen=True)
class DeviceView:
    devices: tuple[Device, ...]
    discovered: tuple[DiscoveredDevice, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "devices": [device.to_dict() for device in self.devices],
            "discovered": [device.to_dict() for device in self.discovered],
        }


@dataclass(frozen=True)
class UartConfig:
    baud_rate: int = 115200

    def to_dict(self) -> dict[str, int]:
        return {"baud_rate": self.baud_rate}


@dataclass(frozen=True)
class ApiKeyRecord:
    id: str
    label: str
    key_hash: str
    revoked: bool = False

    def to_dict(self, include_hash: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "revoked": self.revoked,
        }
        if include_hash:
            data["key_hash"] = self.key_hash
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApiKeyRecord":
        return cls(
            id=str(data["id"]),
            label=str(data.get("label", data["id"])),
            key_hash=str(data["key_hash"]),
            revoked=bool(data.get("revoked", False)),
        )


@dataclass(frozen=True)
class SerialSession:
    device_id: str
    baud_rate: int
    attribution: ReservationAttribution | None = None

    @property
    def id(self) -> str:
        return self.device_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "baud_rate": self.baud_rate,
            "attribution": None if self.attribution is None else self.attribution.to_dict(),
        }


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class DoctorReport:
    ok: bool
    checks: tuple[DoctorCheck, ...]
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "details": dict(self.details),
        }
