"""Power control backends."""

from __future__ import annotations

import subprocess
from typing import Callable, Protocol

from sysbench_devices.errors import HardwareError, ValidationError
from sysbench_devices.models import PowerAction, RuntimeDevice


class PowerBackend(Protocol):
    def set_power(self, runtime: RuntimeDevice, action: PowerAction) -> dict[str, object]:
        ...


class NoopPowerBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def set_power(self, runtime: RuntimeDevice, action: PowerAction) -> dict[str, object]:
        self.calls.append((action.value, runtime.power_target))
        return {"device_id": runtime.id, "action": action.value, "power_target": runtime.power_target}


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class UhubctlPowerBackend:
    def __init__(self, command: str = "uhubctl", runner: CommandRunner | None = None) -> None:
        self.command = command
        self.runner = subprocess.run if runner is None else runner

    def set_power(self, runtime: RuntimeDevice, action: PowerAction) -> dict[str, object]:
        if runtime.power_target is None:
            raise ValidationError(f"device {runtime.id} has no power target")
        hub, port = _split_power_target(runtime.power_target)
        args = build_uhubctl_args(self.command, hub, port, action)
        result = self.runner(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            summary = (result.stderr or result.stdout or "uhubctl failed").strip()
            raise HardwareError(summary)
        return {
            "device_id": runtime.id,
            "action": action.value,
            "power_target": runtime.power_target,
            "args": args,
            "output": result.stdout,
        }


def build_uhubctl_args(command: str, hub: str, port: str, action: PowerAction | str) -> list[str]:
    return [command, "-l", hub, "-p", port, "-a", PowerAction(action).value]


def _split_power_target(power_target: str) -> tuple[str, str]:
    hub, sep, port = power_target.partition(":")
    if not sep or not hub or not port:
        raise ValidationError(f"invalid power target: {power_target}")
    return hub, port
