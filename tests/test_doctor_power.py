import subprocess

from sysbench_devices.doctor import run_doctor
from sysbench_devices.models import PowerAction, RuntimeDevice
from sysbench_devices.power import UhubctlPowerBackend, build_uhubctl_args


def test_doctor_reports_uhubctl_permission_failure(tmp_path):
    fake_uhubctl = tmp_path / "uhubctl"
    fake_uhubctl.write_text("#!/bin/sh\n")
    fake_uhubctl.chmod(0o755)

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="Error initializing USB!")

    report = run_doctor(tmp_path / "registry.toml", tmp_path / "sbdevd.sock", uhubctl=str(fake_uhubctl), runner=runner)

    checks = {check.name: check for check in report.checks}
    assert not report.ok
    assert checks["uhubctl.usable"].detail == "Error initializing USB!"


def test_doctor_reports_uhubctl_usable(tmp_path):
    fake_uhubctl = tmp_path / "uhubctl"
    fake_uhubctl.write_text("#!/bin/sh\n")
    fake_uhubctl.chmod(0o755)

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="Current status for hub 3-7.3\n", stderr="")

    report = run_doctor(tmp_path / "registry.toml", tmp_path / "sbdevd.sock", uhubctl=str(fake_uhubctl), runner=runner)

    checks = {check.name: check for check in report.checks}
    assert checks["uhubctl.usable"].ok
    assert checks["uhubctl.usable"].detail == "Current status for hub 3-7.3"


def test_uhubctl_power_backend_builds_expected_args():
    calls = []

    def runner(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr="")

    backend = UhubctlPowerBackend(runner=runner)
    result = backend.set_power(RuntimeDevice(id="dev", power_target="3-7.3:1"), PowerAction.ON)

    assert calls == [["uhubctl", "-l", "3-7.3", "-p", "1", "-a", "on"]]
    assert result["args"] == calls[0]


def test_build_uhubctl_args_normalizes_action():
    assert build_uhubctl_args("uhubctl", "3-7.3", "1", "cycle") == [
        "uhubctl",
        "-l",
        "3-7.3",
        "-p",
        "1",
        "-a",
        "cycle",
    ]
