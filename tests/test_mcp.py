import base64

from sysbench_devices.mcp import MCPService, build_mcp_server
from sysbench_devices.protocols.cs140e_bootloader import ARM_BASE


class FakeClient:
    def __init__(self):
        self.calls = []

    def devices(self):
        self.calls.append(("devices",))
        return {"devices": [], "discovered": []}

    def reservations(self):
        self.calls.append(("reservations",))
        return []

    def reserve(self, device_id=None, tags=None):
        self.calls.append(("reserve", device_id, tags))
        return {"id": "resv", "device_id": device_id, "attribution": {"kind": "api_key", "id": "test"}}

    def release(self, reservation_id):
        self.calls.append(("release", reservation_id))

    def power(self, device_id, action):
        self.calls.append(("power", device_id, action))
        return {"device_id": device_id, "action": action}

    def open_serial(self, device_id, baud_rate=115200):
        self.calls.append(("open_serial", device_id, baud_rate))
        return {"id": "session", "device_id": device_id, "baud_rate": baud_rate}

    def read_serial(self, session_id, max_bytes=4096, timeout=0.1):
        self.calls.append(("read_serial", session_id, max_bytes, timeout))
        return {"encoding": "base64", "data": ""}

    def write_serial(self, session_id, data, encoding="utf-8"):
        self.calls.append(("write_serial", session_id, data, encoding))
        return {"bytes_written": len(data)}

    def close_serial(self, session_id):
        self.calls.append(("close_serial", session_id))

    def run_serial(self, device_id, data, encoding="utf-8", baud_rate=115200, append_newline=True, max_bytes=4096):
        self.calls.append(("run_serial", device_id, data, encoding, baud_rate, append_newline, max_bytes))
        return {"encoding": "base64", "data": ""}

    def bootload(
        self,
        device_id,
        payload,
        baud_rate=115200,
        timeout=10.0,
        arm_base=ARM_BASE,
        capture_output_seconds=0.0,
        max_output_bytes=4096,
    ):
        self.calls.append(
            (
                "bootload",
                device_id,
                payload,
                baud_rate,
                timeout,
                arm_base,
                capture_output_seconds,
                max_output_bytes,
            )
        )
        return {
            "session_id": "session",
            "bytes_sent": len(payload),
            "crc32": 0,
            "arm_base": arm_base,
            "prints": [],
            "captured_output": {"encoding": "base64", "data": ""},
        }


def test_mcp_service_uses_http_sdk_surface():
    client = FakeClient()
    service = MCPService(client)

    assert service.list_devices() == {"devices": [], "discovered": []}
    assert service.reserve(tags=["fpga"])["id"] == "resv"
    assert service.release("resv") == {"reservation_id": "resv"}
    assert service.serial_close("session") == {"session_id": "session"}

    assert ("devices",) in client.calls
    assert ("reserve", None, ["fpga"]) in client.calls
    assert ("release", "resv") in client.calls
    assert ("close_serial", "session") in client.calls


def test_mcp_service_exposes_bootloader_via_http_sdk() -> None:
    client = FakeClient()
    service = MCPService(client)
    payload = b"kernel image"

    result = service.bootload_binary(
        device_id="a4c91f2b",
        data=base64.b64encode(payload).decode("ascii"),
        baud_rate=230400,
        timeout=3.0,
        capture_output_seconds=0.25,
        max_output_bytes=128,
    )

    assert result["bytes_sent"] == len(payload)
    assert (
        "bootload",
        "a4c91f2b",
        payload,
        230400,
        3.0,
        ARM_BASE,
        0.25,
        128,
    ) in client.calls


def test_build_mcp_server_returns_fastmcp_instance():
    server = build_mcp_server(MCPService(FakeClient()))

    assert server.name == "Sysbench Device Manager"
