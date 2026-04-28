from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sysbench_devices.api_keys import create_api_key
from sysbench_devices.client import SysbenchDevicesClient
from sysbench_devices.discovery import DiscoveryBackend
from sysbench_devices.http import SysbenchHTTPServer
from sysbench_devices.models import DeviceRegistration, RuntimeDevice
from sysbench_devices.registry import RegistryData, RegistryStore
from sysbench_devices.rpc import SocketRPCClient, SocketRPCServer, dispatch_socket_method
from sysbench_devices.state import DeviceStateStore


class StaticDiscovery(DiscoveryBackend):
    def __init__(self, *runtimes: RuntimeDevice) -> None:
        self.runtimes = runtimes

    def discover(self) -> tuple[RuntimeDevice, ...]:
        return self.runtimes


def make_state(tmp_path):
    store = RegistryStore(tmp_path / "registry.toml")
    key = create_api_key("autograder", "Autograder")
    other_key = create_api_key("worker", "Worker")
    store.save(
        RegistryData(
            devices=(DeviceRegistration(id="a4c91f2b", name="board", tags=("fpga", "uart")),),
            api_keys=(key.record, other_key.record),
        )
    )
    state = DeviceStateStore(
        registry=store,
        discovery=StaticDiscovery(RuntimeDevice(id="a4c91f2b", serial_port="/dev/ttyUSB0", power_target="1-1:1")),
    )
    return state, key.secret, other_key.secret


def test_socket_reservation_uses_admin_attribution(tmp_path):
    state, _secret, _other_secret = make_state(tmp_path)

    reservation = dispatch_socket_method(state, "reserve", {"device_id": "a4c91f2b"})

    assert reservation["attribution"]["kind"] == "socket"
    assert reservation["attribution"]["id"] == "admin"


def test_socket_rpc_server_smoke(tmp_path):
    state, _secret, _other_secret = make_state(tmp_path)
    socket_path = tmp_path / "sbdevd.sock"
    server = SocketRPCServer(socket_path, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = SocketRPCClient(socket_path)
        result = client.call("devices")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["devices"][0]["id"] == "a4c91f2b"


def test_http_reservation_uses_api_key_attribution(tmp_path):
    state, secret, _other_secret = make_state(tmp_path)
    server = SysbenchHTTPServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        client = SysbenchDevicesClient(base_url=base_url, api_key=secret)
        reservation = client.reserve(device_id="a4c91f2b")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert reservation["attribution"]["kind"] == "api_key"
    assert reservation["attribution"]["id"] == "autograder"


def test_http_rejects_reservation_without_api_key(tmp_path):
    state, _secret, _other_secret = make_state(tmp_path)
    server = SysbenchHTTPServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/reservations",
            data=json.dumps({"device_id": "a4c91f2b"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=10)
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("expected HTTP 401")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_rejects_serial_read_without_api_key(tmp_path):
    state, secret, _other_secret = make_state(tmp_path)
    server = SysbenchHTTPServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        session = SysbenchDevicesClient(base_url=base_url, api_key=secret).open_serial("a4c91f2b")
        request = Request(
            f"{base_url}/serial/sessions/{session['id']}/read",
            method="GET",
        )
        try:
            urlopen(request, timeout=10)
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("expected HTTP 401")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_rejects_release_by_different_api_key(tmp_path):
    state, secret, other_secret = make_state(tmp_path)
    server = SysbenchHTTPServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        owner = SysbenchDevicesClient(base_url=base_url, api_key=secret)
        other = SysbenchDevicesClient(base_url=base_url, api_key=other_secret)
        reservation = owner.reserve(device_id="a4c91f2b")
        try:
            other.release(reservation["id"])
        except RuntimeError as exc:
            assert "conflict" in str(exc)
        else:
            raise AssertionError("expected release by different API key to fail")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert [reservation.id for reservation in state.list_reservations()] == [reservation["id"]]


def test_socket_admin_can_release_api_key_reservation(tmp_path):
    state, secret, _other_secret = make_state(tmp_path)
    reservation = state.reserve(state.attribution_for_api_key(secret), device_id="a4c91f2b")

    dispatch_socket_method(state, "release", {"reservation_id": reservation.id})

    assert state.list_reservations() == ()
