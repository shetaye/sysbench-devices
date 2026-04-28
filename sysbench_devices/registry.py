"""TOML registry storage for devices and API keys."""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sysbench_devices.errors import ValidationError
from sysbench_devices.models import ApiKeyRecord, DeviceRegistration

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_HEX_ID_RE = re.compile(r"^[0-9a-f]{4,32}$")


@dataclass(frozen=True)
class RegistryData:
    devices: tuple[DeviceRegistration, ...] = ()
    api_keys: tuple[ApiKeyRecord, ...] = ()

    def device_by_id(self) -> dict[str, DeviceRegistration]:
        return {device.id: device for device in self.devices}

    def api_key_by_id(self) -> dict[str, ApiKeyRecord]:
        return {key.id: key for key in self.api_keys}


class RegistryStore:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def load(self) -> RegistryData:
        if not self.path.exists():
            return RegistryData()
        with self.path.open("rb") as file:
            raw = tomllib.load(file)
        return parse_registry(raw)

    def save(self, data: RegistryData) -> None:
        validate_registry(data)
        text = dump_registry(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            delete=False,
        ) as file:
            tmp_name = file.name
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_name, self.path)


def parse_registry(raw: dict[str, Any]) -> RegistryData:
    devices = tuple(DeviceRegistration.from_dict(item) for item in raw.get("devices", []))
    api_keys = tuple(ApiKeyRecord.from_dict(item) for item in raw.get("api_keys", []))
    data = RegistryData(devices=devices, api_keys=api_keys)
    validate_registry(data)
    return data


def validate_registry(data: RegistryData) -> None:
    seen_devices: set[str] = set()
    for device in data.devices:
        if not _HEX_ID_RE.fullmatch(device.id):
            raise ValidationError(f"invalid device id: {device.id}")
        if device.id in seen_devices:
            raise ValidationError(f"duplicate device id: {device.id}")
        seen_devices.add(device.id)
        if len(set(device.tags)) != len(device.tags):
            raise ValidationError(f"duplicate tag on device {device.id}")
        for tag in device.tags:
            if not tag or tag.strip() != tag or any(ch.isspace() for ch in tag):
                raise ValidationError(f"invalid tag on device {device.id}: {tag!r}")

    seen_keys: set[str] = set()
    for key in data.api_keys:
        if not _ID_RE.fullmatch(key.id):
            raise ValidationError(f"invalid api key id: {key.id}")
        if key.id == "admin":
            raise ValidationError("api key id 'admin' is reserved for Unix socket attribution")
        if key.id in seen_keys:
            raise ValidationError(f"duplicate api key id: {key.id}")
        seen_keys.add(key.id)
        if not key.label:
            raise ValidationError(f"api key {key.id} has an empty label")
        if "$" not in key.key_hash:
            raise ValidationError(f"api key {key.id} has a malformed hash")


def dump_registry(data: RegistryData) -> str:
    validate_registry(data)
    lines: list[str] = []
    for device in data.devices:
        lines.append("[[devices]]")
        lines.append(f'id = "{_escape(device.id)}"')
        if device.name is not None:
            lines.append(f'name = "{_escape(device.name)}"')
        tags = ", ".join(f'"{_escape(tag)}"' for tag in device.tags)
        lines.append(f"tags = [{tags}]")
        lines.append("")

    for key in data.api_keys:
        lines.append("[[api_keys]]")
        lines.append(f'id = "{_escape(key.id)}"')
        lines.append(f'label = "{_escape(key.label)}"')
        lines.append(f'key_hash = "{_escape(key.key_hash)}"')
        if key.revoked:
            lines.append("revoked = true")
        lines.append("")

    return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
