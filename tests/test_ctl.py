from argparse import Namespace

from sysbench_devices.ctl import _format_result


def test_format_doctor_verbose_includes_remediation_hint():
    result = {
        "ok": False,
        "checks": [
            {
                "name": "uhubctl.usable",
                "ok": False,
                "detail": "Error initializing USB!",
            }
        ],
    }

    text = _format_result(result, Namespace(command="doctor", verbose=True))

    assert "Doctor: unhealthy" in text
    assert "fail uhubctl.usable" in text
    assert "Configure udev/group permissions" in text


def test_format_devices_uses_table_for_registered_and_discovered():
    result = {
        "devices": [
            {
                "id": "abcd1234",
                "state": "online",
                "name": "board",
                "tags": ["fpga", "uart"],
                "runtime": {
                    "serial_port": "/dev/ttyUSB0",
                    "power_target": "3-7.3:1",
                },
                "reservation": None,
            }
        ],
        "discovered": [
            {
                "id": "ef567890",
                "runtime": {
                    "serial_port": "/dev/ttyUSB1",
                    "power_target": "3-7.3:2",
                    "usb_serial": "serial2",
                    "usb_path": "3-7.3.2",
                },
            }
        ],
    }

    text = _format_result(result, Namespace(command="devices", verbose=False))

    assert "ID" in text
    assert "abcd1234" in text
    assert "/dev/ttyUSB0" in text
    assert "Discovered unregistered devices:" in text
    assert "ef567890" in text


def test_format_reservations_empty():
    text = _format_result([], Namespace(command="reservations", verbose=False))

    assert text == "No active reservations."


def test_format_register_and_reserve_mutation_outputs():
    registration = {"id": "abcd1234", "name": "board", "tags": ["fpga", "uart"]}
    reservation = {
        "id": "resv1234",
        "device_id": "abcd1234",
        "attribution": {"kind": "socket", "id": "admin", "label": "Unix socket admin"},
    }

    register_text = _format_result(registration, Namespace(command="register", verbose=False))
    reserve_text = _format_result(reservation, Namespace(command="reserve", verbose=False))

    assert "Registered device: abcd1234" in register_text
    assert "Tags: fpga, uart" in register_text
    assert "Reserved device: abcd1234" in reserve_text
    assert "Attribution: socket:admin" in reserve_text


def test_format_power_and_serial_outputs():
    power_text = _format_result(
        {
            "device_id": "abcd1234",
            "action": "on",
            "power_target": "3-7.3:1",
            "args": ["uhubctl", "-l", "3-7.3", "-p", "1", "-a", "on"],
        },
        Namespace(command="power", verbose=False),
    )
    serial_text = _format_result(
        {
            "id": "sess1234",
            "device_id": "abcd1234",
            "baud_rate": 115200,
            "attribution": {"kind": "socket", "id": "admin", "label": "Unix socket admin"},
        },
        Namespace(command="serial", serial_command="open", verbose=False),
    )

    assert "Power on: abcd1234" in power_text
    assert "Command: uhubctl -l 3-7.3 -p 1 -a on" in power_text
    assert "Opened serial session: sess1234" in serial_text
    assert "Attribution: socket:admin" in serial_text


def test_format_api_key_create_and_list():
    create_text = _format_result(
        {
            "api_key": {"id": "autograder", "label": "Autograder", "revoked": False},
            "secret": "sbdev_secret",
        },
        Namespace(command="api-keys", api_key_command="create", verbose=False),
    )
    list_text = _format_result(
        [{"id": "autograder", "label": "Autograder", "revoked": False}],
        Namespace(command="api-keys", api_key_command="list", verbose=False),
    )

    assert "Created API key: autograder" in create_text
    assert "Secret: sbdev_secret" in create_text
    assert "autograder" in list_text
    assert "Autograder" in list_text
