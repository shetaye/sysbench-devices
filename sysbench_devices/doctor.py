"""Runtime diagnostics."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from sysbench_devices.discovery import HubPort, SerialPortProvider, SerialPortRecord, list_serial_ports, parse_uhubctl_output
from sysbench_devices.models import DoctorCheck, DoctorReport
from sysbench_devices.registry import RegistryStore

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def run_doctor(
    registry_path: str | os.PathLike[str],
    socket_path: str | os.PathLike[str],
    uhubctl: str = "uhubctl",
    runner: CommandRunner | None = None,
    serial_ports: SerialPortProvider | None = None,
) -> DoctorReport:
    registry = Path(registry_path)
    socket = Path(socket_path)
    runner = subprocess.run if runner is None else runner
    serial_ports = list_serial_ports if serial_ports is None else serial_ports
    uhubctl_path = shutil.which(uhubctl)
    uhubctl_check, uhubctl_output = _check_uhubctl_usable(uhubctl if uhubctl_path is None else uhubctl_path, runner)
    checks = [
        DoctorCheck(
            name="uhubctl.installed",
            ok=uhubctl_path is not None,
            detail=uhubctl_path if uhubctl_path is not None else "not found on PATH",
        ),
        uhubctl_check,
        DoctorCheck(
            name="registry",
            ok=registry.exists() or os.access(registry.parent, os.W_OK),
            detail=str(registry),
        ),
        DoctorCheck(
            name="socket_path",
            ok=socket.parent.exists() or os.access(socket.parent.parent, os.W_OK),
            detail=str(socket),
        ),
    ]
    details = {
        "serial_ports": [_serial_port_to_dict(record) for record in serial_ports()],
        "uhubctl_devices": [_hub_port_to_dict(port) for port in parse_uhubctl_output(uhubctl_output) if port.is_target_device],
        "registry_devices": [device.to_dict() for device in RegistryStore(registry).load().devices],
    }
    return DoctorReport(ok=all(check.ok for check in checks), checks=tuple(checks), details=details)


def _check_uhubctl_usable(command: str, runner: CommandRunner) -> tuple[DoctorCheck, str]:
    try:
        result = runner(
            [command],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except FileNotFoundError:
        return DoctorCheck(name="uhubctl.usable", ok=False, detail="not found on PATH"), ""
    except subprocess.TimeoutExpired:
        return DoctorCheck(name="uhubctl.usable", ok=False, detail="timed out running uhubctl"), ""

    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        detail = output or f"uhubctl exited with status {result.returncode}"
        return DoctorCheck(name="uhubctl.usable", ok=False, detail=detail), output
    first_line = output.splitlines()[0] if output else "uhubctl ran successfully"
    return DoctorCheck(name="uhubctl.usable", ok=True, detail=first_line), output


def _serial_port_to_dict(record: SerialPortRecord) -> dict[str, Any]:
    return {
        "device": record.device,
        "vid": record.vid,
        "pid": record.pid,
        "serial_number": record.serial_number,
        "location": record.location,
        "normalized_location": record.normalized_location,
        "manufacturer": record.manufacturer,
        "product": record.product,
        "hwid": record.hwid,
    }


def _hub_port_to_dict(port: HubPort) -> dict[str, Any]:
    connected = port.connected_device
    return {
        "power_target": port.power_target,
        "usb_path": port.usb_path,
        "status": port.status,
        "vid": None if connected is None else connected.vid,
        "pid": None if connected is None else connected.pid,
        "vid_pid": None if connected is None else connected.vid_pid,
        "product": None if connected is None else connected.product,
        "serial": None if connected is None else connected.serial,
    }
