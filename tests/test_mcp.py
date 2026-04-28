from collections import deque
from pathlib import Path

from sysbench_devices.mcp import MCPService, build_mcp_server
from sysbench_devices.protocols.cs140e_bootloader import ARM_BASE


class FakeStream:
    def __init__(self):
        self.reads = deque([b"hello"])
        self.writes = []

    def read(self, max_bytes=4096, timeout=0.1):
        chunk = self.reads.popleft() if self.reads else b""
        return chunk[:max_bytes]

    def write(self, data):
        self.writes.append(data)


class FakeStreamContext:
    def __init__(self, client, device_id):
        self.client = client
        self.device_id = device_id

    def __enter__(self):
        self.client.calls.append(("stream_enter", self.device_id))
        return self.client.streams[self.device_id]

    def __exit__(self, exc_type, exc, traceback):
        self.client.calls.append(("stream_exit", self.device_id))


class FakeClient:
    def __init__(self):
        self.calls = []
        self.streams = {}

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
        self.streams[device_id] = FakeStream()
        return {"device_id": device_id, "baud_rate": baud_rate}

    def serial_stream(self, device_id):
        self.calls.append(("serial_stream", device_id))
        return FakeStreamContext(self, device_id)

    def close_serial(self, device_id):
        self.calls.append(("close_serial", device_id))

    def bootload_file(
        self,
        stream,
        path,
        timeout=10.0,
        arm_base=ARM_BASE,
        capture_output_seconds=0.0,
        max_output_bytes=4096,
    ):
        self.calls.append(
            (
                "bootload_file",
                stream,
                path,
                timeout,
                arm_base,
                capture_output_seconds,
                max_output_bytes,
            )
        )
        return {
            "device_id": "board",
            "bytes_sent": len(Path(path).read_bytes()),
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
    session = service.open_serial("board", baud_rate=230400)
    assert service.read_serial(session["device_id"]) == {"encoding": "base64", "data": "aGVsbG8="}
    assert service.write_serial(session["device_id"], "70696e67", encoding="hex") == {"bytes_written": 4}
    assert client.streams["board"].writes == [b"ping"]
    assert service.close_serial("board") == {"device_id": "board"}

    assert ("devices",) in client.calls
    assert ("reserve", None, ["fpga"]) in client.calls
    assert ("release", "resv") in client.calls
    assert ("open_serial", "board", 230400) in client.calls
    assert ("serial_stream", "board") in client.calls
    assert ("stream_exit", "board") in client.calls
    assert ("close_serial", "board") in client.calls


def test_mcp_service_exposes_bootloader_for_open_serial_stream(tmp_path) -> None:
    client = FakeClient()
    service = MCPService(client)
    binary_path = tmp_path / "kernel.bin"
    binary_path.write_bytes(b"kernel image")
    session = service.open_serial("board")

    result = service.bootload_file(
        device_id=session["device_id"],
        binary_path=str(binary_path),
        timeout=3.0,
        capture_output_seconds=0.25,
        max_output_bytes=128,
    )

    assert result["bytes_sent"] == len(b"kernel image")
    assert client.calls[-1] == (
        "bootload_file",
        client.streams["board"],
        str(binary_path),
        3.0,
        ARM_BASE,
        0.25,
        128,
    )


def test_build_mcp_server_returns_fastmcp_instance():
    server = build_mcp_server(MCPService(FakeClient()))

    assert server.name == "Sysbench Device Manager"
