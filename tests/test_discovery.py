from pathlib import Path

from sysbench_devices.discovery import (
    HostDiscoveryBackend,
    SerialPortRecord,
    discover_runtime_devices,
    parse_uhubctl_output,
    stable_device_id,
)


FIXTURE = Path(__file__).parent / "fixtures" / "uhubctl-via.txt"


def test_parse_uhubctl_output():
    ports = parse_uhubctl_output(
        """
Current status for hub 1-1 [1234:abcd]
  Port 1: 0100 power
  Port 2: 0000 off
Current status for hub 2-1 [1234:abcd]
  Port 3: 0100 power
"""
    )

    assert [port.power_target for port in ports] == ["1-1:1", "1-1:2", "2-1:3"]


def test_parse_real_via_hub_topology():
    ports = parse_uhubctl_output(FIXTURE.read_text())

    assert len(ports) == 16
    cp210_ports = [port for port in ports if port.is_target_device]
    assert [port.power_target for port in cp210_ports] == [
        "3-7.3:1",
        "3-7.3:2",
        "3-7.3:3",
        "3-7.3:4",
    ]
    assert cp210_ports[0].connected_device is not None
    assert cp210_ports[0].connected_device.vid_pid == "10c4:ea60"
    assert cp210_ports[0].connected_device.serial == "1c762f96c112f01180c07074801f8d56"
    assert all(not port.connected_device or not port.connected_device.is_hub for port in cp210_ports)
    assert not next(port for port in ports if port.power_target == "3-7:3").is_target_device


def test_discover_runtime_devices_joins_by_usb_serial():
    ports = parse_uhubctl_output(FIXTURE.read_text())
    serials = (
        SerialPortRecord(
            device="/dev/ttyUSB0",
            vid="10c4",
            pid="ea60",
            serial_number="1c762f96c112f01180c07074801f8d56",
            location="3-7.3.1",
            manufacturer="Silicon Labs",
            product="CP2102N USB to UART Bridge Controller",
        ),
    )

    runtimes = discover_runtime_devices(ports, serials)
    runtime = next(item for item in runtimes if item.usb_serial == "1c762f96c112f01180c07074801f8d56")

    assert runtime.serial_port == "/dev/ttyUSB0"
    assert runtime.power_target == "3-7.3:1"
    assert runtime.usb_path == "3-7.3.1"
    assert runtime.metadata["connected_device"]["vid"] == "10c4"
    assert runtime.metadata["hub"]["vid"] == "2109"


def test_host_discovery_uses_runner_and_serial_provider():
    class Result:
        returncode = 0
        stdout = FIXTURE.read_text()
        stderr = ""

    backend = HostDiscoveryBackend(
        runner=lambda *args, **kwargs: Result(),
        serial_ports=lambda: (
            SerialPortRecord(
                device="/dev/ttyUSB1",
                serial_number="1cd52172c112f011b2696574801f8d56",
                location="3-7.3.2",
            ),
        ),
    )

    runtimes = backend.discover()

    assert any(runtime.serial_port == "/dev/ttyUSB1" and runtime.power_target == "3-7.3:2" for runtime in runtimes)


def test_stable_device_id_is_short_hex():
    first = stable_device_id("hub=1-1;port=1")
    second = stable_device_id("hub=1-1;port=1")

    assert first == second
    assert len(first) == 8
    int(first, 16)
