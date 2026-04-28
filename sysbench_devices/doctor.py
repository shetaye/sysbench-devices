"""Runtime diagnostics."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from sysbench_devices.models import DoctorCheck, DoctorReport

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def run_doctor(
    registry_path: str | os.PathLike[str],
    socket_path: str | os.PathLike[str],
    uhubctl: str = "uhubctl",
    runner: CommandRunner | None = None,
) -> DoctorReport:
    registry = Path(registry_path)
    socket = Path(socket_path)
    runner = subprocess.run if runner is None else runner
    uhubctl_path = shutil.which(uhubctl)
    checks = [
        DoctorCheck(
            name="uhubctl.installed",
            ok=uhubctl_path is not None,
            detail=uhubctl_path if uhubctl_path is not None else "not found on PATH",
        ),
        _check_uhubctl_usable(uhubctl if uhubctl_path is None else uhubctl_path, runner),
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
    return DoctorReport(ok=all(check.ok for check in checks), checks=tuple(checks))


def _check_uhubctl_usable(command: str, runner: CommandRunner) -> DoctorCheck:
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
        return DoctorCheck(name="uhubctl.usable", ok=False, detail="not found on PATH")
    except subprocess.TimeoutExpired:
        return DoctorCheck(name="uhubctl.usable", ok=False, detail="timed out running uhubctl")

    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return DoctorCheck(
            name="uhubctl.usable",
            ok=False,
            detail=output or f"uhubctl exited with status {result.returncode}",
        )
    first_line = output.splitlines()[0] if output else "uhubctl ran successfully"
    return DoctorCheck(name="uhubctl.usable", ok=True, detail=first_line)
