"""Hardware discovery helpers."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from sysbench_devices.models import RuntimeDevice


@dataclass(frozen=True)
class Hub:
    location: str
    vid: str | None = None
    pid: str | None = None
    description: str | None = None

    @property
    def vid_pid(self) -> str | None:
        if self.vid is None or self.pid is None:
            return None
        return f"{self.vid}:{self.pid}"


@dataclass(frozen=True)
class ConnectedUsbDevice:
    vid: str
    pid: str
    product: str
    serial: str | None = None

    @property
    def vid_pid(self) -> str:
        return f"{self.vid}:{self.pid}"

    @property
    def is_hub(self) -> bool:
        return "hub" in self.product.casefold()


@dataclass(frozen=True)
class HubPort:
    hub: str
    port: str
    status: str
    hub_info: Hub | None = None
    status_code: str | None = None
    flags: tuple[str, ...] = ()
    connected_device: ConnectedUsbDevice | None = None

    @property
    def power_target(self) -> str:
        return f"{self.hub}:{self.port}"

    @property
    def usb_path(self) -> str:
        return f"{self.hub}.{self.port}"

    @property
    def is_powered(self) -> bool:
        return "power" in self.flags

    @property
    def is_connected(self) -> bool:
        return "connect" in self.flags

    @property
    def is_target_device(self) -> bool:
        return self.is_powered and self.is_connected and self.connected_device is not None and not self.connected_device.is_hub


@dataclass(frozen=True)
class SerialPortRecord:
    device: str
    vid: str | None = None
    pid: str | None = None
    serial_number: str | None = None
    location: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    hwid: str | None = None

    @property
    def normalized_location(self) -> str | None:
        return normalize_usb_location(self.location)


class DiscoveryBackend(Protocol):
    def discover(self) -> tuple[RuntimeDevice, ...]:
        ...


class EmptyDiscoveryBackend:
    def discover(self) -> tuple[RuntimeDevice, ...]:
        return ()


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
SerialPortProvider = Callable[[], tuple[SerialPortRecord, ...]]


class HostDiscoveryBackend:
    def __init__(
        self,
        uhubctl: str = "uhubctl",
        runner: CommandRunner | None = None,
        serial_ports: SerialPortProvider | None = None,
    ) -> None:
        self.uhubctl = uhubctl
        self.runner = subprocess.run if runner is None else runner
        self.serial_ports = list_serial_ports if serial_ports is None else serial_ports

    def discover(self) -> tuple[RuntimeDevice, ...]:
        try:
            result = self.runner(
                [self.uhubctl],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError:
            return ()
        if result.returncode != 0:
            return ()
        ports = parse_uhubctl_output(result.stdout)
        serial_ports = self.serial_ports()
        runtimes = discover_runtime_devices(ports, serial_ports)
        return tuple(runtimes)


def discover_runtime_devices(
    ports: tuple[HubPort, ...],
    serial_ports: tuple[SerialPortRecord, ...],
) -> tuple[RuntimeDevice, ...]:
    serial_by_number = {
        record.serial_number: record
        for record in serial_ports
        if record.serial_number
    }
    serial_by_location = {
        record.normalized_location: record
        for record in serial_ports
        if record.normalized_location
    }
    used_serial_devices: set[str] = set()
    runtimes: list[RuntimeDevice] = []

    for port in ports:
        if not port.is_target_device:
            continue
        serial_record = match_serial_port(port, serial_by_number, serial_by_location)
        if serial_record is not None:
            used_serial_devices.add(serial_record.device)
        serial_number = (
            None if port.connected_device is None else port.connected_device.serial
        ) or (None if serial_record is None else serial_record.serial_number)
        identity = f"usb_path={port.usb_path};usb_serial={serial_number or ''}"
        runtimes.append(
            RuntimeDevice(
                id=stable_device_id(identity),
                serial_port=None if serial_record is None else serial_record.device,
                power_target=port.power_target,
                usb_path=port.usb_path,
                usb_serial=serial_number,
                metadata=_runtime_metadata(port, serial_record),
            )
        )

    for record in serial_ports:
        if record.device in used_serial_devices:
            continue
        identity = f"serial={record.serial_number or ''};location={record.normalized_location or ''};device={record.device}"
        runtimes.append(
            RuntimeDevice(
                id=stable_device_id(identity),
                serial_port=record.device,
                usb_path=record.normalized_location,
                usb_serial=record.serial_number,
                metadata={"serial": _serial_record_metadata(record)},
            )
        )

    return tuple(sorted(runtimes, key=lambda runtime: runtime.id))


def match_serial_port(
    port: HubPort,
    serial_by_number: dict[str, SerialPortRecord],
    serial_by_location: dict[str, SerialPortRecord],
) -> SerialPortRecord | None:
    if port.connected_device is not None and port.connected_device.serial:
        match = serial_by_number.get(port.connected_device.serial)
        if match is not None:
            return match
    port_location = normalize_usb_location(port.usb_path)
    if port_location is not None:
        match = serial_by_location.get(port_location)
        if match is not None:
            return match
        for location, record in serial_by_location.items():
            if location and (location == port_location or location.startswith(f"{port_location}:")):
                return record
    return None


def stable_device_id(identity: str, length: int = 8) -> str:
    return hashlib.blake2s(identity.encode("utf-8"), digest_size=16).hexdigest()[:length]


def parse_uhubctl_output(output: str) -> tuple[HubPort, ...]:
    current_hub: Hub | None = None
    ports: list[HubPort] = []
    for line in output.splitlines():
        hub_match = re.search(r"Current status for hub ([^\s]+)(?:\s+\[([^\]]+)\])?", line)
        if hub_match:
            current_hub = _parse_hub(hub_match.group(1).rstrip(":"), hub_match.group(2))
            continue
        port_match = re.search(r"Port\s+(\d+):\s+(.+)$", line)
        if current_hub and port_match:
            ports.append(_parse_port(current_hub, port_match.group(1), port_match.group(2).strip()))
    return tuple(ports)


def list_serial_ports() -> tuple[SerialPortRecord, ...]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return ()
    records = []
    for port in list_ports.comports():
        record = SerialPortRecord(
            device=str(port.device),
            vid=_hex4(getattr(port, "vid", None)),
            pid=_hex4(getattr(port, "pid", None)),
            serial_number=_none_or_str(getattr(port, "serial_number", None)),
            location=_none_or_str(getattr(port, "location", None)),
            manufacturer=_none_or_str(getattr(port, "manufacturer", None)),
            product=_none_or_str(getattr(port, "product", None)),
            hwid=_none_or_str(getattr(port, "hwid", None)),
        )
        if _is_usb_serial_record(record):
            records.append(record)
    return tuple(records)


def normalize_usb_location(location: str | None) -> str | None:
    if not location:
        return None
    normalized = location.removeprefix("usb-")
    normalized = normalized.split(":", 1)[0]
    normalized = normalized.strip()
    return normalized or None


def _parse_hub(location: str, bracket: str | None) -> Hub:
    vid = pid = description = None
    if bracket:
        parsed = _parse_bracketed_device(bracket)
        if parsed is not None:
            vid = parsed.vid
            pid = parsed.pid
            description = parsed.product
    return Hub(location=location, vid=vid, pid=pid, description=description)


def _parse_port(hub: Hub, port_number: str, raw_status: str) -> HubPort:
    status_text, bracket = _split_status_and_bracket(raw_status)
    parts = status_text.split()
    status_code = parts[0] if parts else None
    flags = tuple(part for part in parts[1:] if part)
    return HubPort(
        hub=hub.location,
        port=port_number,
        status=raw_status,
        hub_info=hub,
        status_code=status_code,
        flags=flags,
        connected_device=_parse_bracketed_device(bracket) if bracket else None,
    )


def _split_status_and_bracket(status: str) -> tuple[str, str | None]:
    match = re.match(r"(?P<status>.*?)(?:\s+\[(?P<bracket>[^\]]+)\])?$", status)
    if not match:
        return status, None
    return match.group("status").strip(), match.group("bracket")


def _parse_bracketed_device(value: str | None) -> ConnectedUsbDevice | None:
    if not value:
        return None
    match = re.match(r"(?P<vid>[0-9a-fA-F]{4}):(?P<pid>[0-9a-fA-F]{4})(?:\s+(?P<rest>.*))?$", value.strip())
    if not match:
        return None
    rest = (match.group("rest") or "").strip()
    product = rest
    serial = None
    if rest:
        product, serial = _split_product_and_serial(rest)
    return ConnectedUsbDevice(
        vid=match.group("vid").lower(),
        pid=match.group("pid").lower(),
        product=product,
        serial=serial,
    )


def _split_product_and_serial(rest: str) -> tuple[str, str | None]:
    product, sep, maybe_serial = rest.rpartition(" ")
    if sep and re.fullmatch(r"[A-Za-z0-9]{8,}", maybe_serial):
        return product.strip(), maybe_serial
    return rest, None


def _runtime_metadata(port: HubPort, serial_record: SerialPortRecord | None) -> dict[str, Any]:
    connected = port.connected_device
    hub = port.hub_info
    return {
        "hub": None
        if hub is None
        else {
            "location": hub.location,
            "vid": hub.vid,
            "pid": hub.pid,
            "description": hub.description,
        },
        "hub_port": port.port,
        "uhubctl_status": port.status,
        "connected_device": None
        if connected is None
        else {
            "vid": connected.vid,
            "pid": connected.pid,
            "product": connected.product,
            "serial": connected.serial,
        },
        "serial": None if serial_record is None else _serial_record_metadata(serial_record),
    }


def _serial_record_metadata(record: SerialPortRecord) -> dict[str, str | None]:
    return {
        "device": record.device,
        "vid": record.vid,
        "pid": record.pid,
        "serial_number": record.serial_number,
        "location": record.location,
        "manufacturer": record.manufacturer,
        "product": record.product,
        "hwid": record.hwid,
    }


def _is_usb_serial_record(record: SerialPortRecord) -> bool:
    if record.vid or record.pid or record.serial_number or record.normalized_location:
        return True
    if record.device.startswith(("/dev/ttyUSB", "/dev/ttyACM", "COM")) and record.hwid != "n/a":
        return True
    return False


def _hex4(value: object) -> str | None:
    if value is None:
        return None
    try:
        return f"{int(value):04x}"
    except (TypeError, ValueError):
        return str(value).lower()


def _none_or_str(value: object) -> str | None:
    return None if value is None else str(value)
