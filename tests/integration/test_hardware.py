import os
import time

import pytest

from sysbench_devices.discovery import HostDiscoveryBackend
from sysbench_devices.models import PowerAction
from sysbench_devices.power import UhubctlPowerBackend
from sysbench_devices.registry import RegistryStore
from sysbench_devices.serial import PySerialBackend
from sysbench_devices.state import DeviceStateStore, admin_attribution


pytestmark = pytest.mark.skipif(
    os.environ.get("SBDEV_HARDWARE") != "1",
    reason="set SBDEV_HARDWARE=1 to run hardware integration tests",
)


def _live_runtimes():
    runtimes = tuple(sorted(
        (runtime for runtime in HostDiscoveryBackend().discover() if runtime.power_target),
        key=lambda runtime: runtime.power_target or "",
    ))
    if not runtimes:
        pytest.skip("no power-controllable USB serial devices discovered")
    return runtimes


def _wait_for_runtime_ids(expected: dict[str, str], timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = {
            runtime.id: runtime
            for runtime in HostDiscoveryBackend().discover()
            if runtime.id in expected
        }
        if set(current) == set(expected):
            return current
        time.sleep(0.5)
    missing = sorted(set(expected) - set(current))
    pytest.fail(f"timed out waiting for devices to reappear: {missing}")


def test_live_discovery_finds_power_controllable_serial_devices():
    runtimes = _live_runtimes()

    assert all(runtime.serial_port for runtime in runtimes)
    assert all(runtime.power_target for runtime in runtimes)
    assert all(runtime.usb_serial for runtime in runtimes)


def test_live_power_on_is_safe_noop_for_first_discovered_device():
    runtime = _live_runtimes()[0]

    result = UhubctlPowerBackend().set_power(runtime, PowerAction.ON)

    assert result["action"] == "on"
    assert result["power_target"] == runtime.power_target


def test_live_state_register_reserve_and_serial_open_close(tmp_path):
    runtime = _live_runtimes()[0]
    state = DeviceStateStore(
        registry=RegistryStore(tmp_path / "registry.toml"),
        discovery=HostDiscoveryBackend(),
        serial=PySerialBackend(),
    )

    registration = state.register(runtime.id, name="hardware-test", tags=("hardware",))
    reservation = state.reserve(admin_attribution(), device_id=registration.id)
    session = state.open_serial(registration.id, attribution=admin_attribution())
    state.close_serial(session.id, attribution=admin_attribution())

    assert reservation.device_id == registration.id
    assert session.device_id == registration.id


@pytest.mark.skipif(
    os.environ.get("SBDEV_HARDWARE_DESTRUCTIVE") != "1",
    reason="set SBDEV_HARDWARE_DESTRUCTIVE=1 to run off/on hardware tests",
)
def test_live_destructive_two_devices_keep_ids_on_same_ports_after_off_on():
    runtimes = _live_runtimes()
    if len(runtimes) < 2:
        pytest.skip("need at least two power-controllable devices")

    selected = runtimes[:2]
    expected_targets = {runtime.id: runtime.power_target for runtime in selected}
    power = UhubctlPowerBackend()

    try:
        for runtime in selected:
            power.set_power(runtime, PowerAction.OFF)
        time.sleep(1.0)
    finally:
        for runtime in selected:
            power.set_power(runtime, PowerAction.ON)

    rediscovered = _wait_for_runtime_ids(expected_targets)

    assert {
        runtime_id: runtime.power_target
        for runtime_id, runtime in rediscovered.items()
    } == expected_targets
