from __future__ import annotations

import socket
import stat
import threading
import time

import pytest
from fastapi.testclient import TestClient

from sysbench_devices.errors import ConflictError
from sysbench_devices.api_keys import create_api_key
from sysbench_devices.client import SysbenchDevicesClient
from sysbench_devices.discovery import DiscoveryBackend
from sysbench_devices.http import build_http_app, build_http_server
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
        socket_mode = stat.S_IMODE(socket_path.stat().st_mode)
        client = SocketRPCClient(socket_path)
        result = client.call("devices")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["devices"][0]["id"] == "a4c91f2b"
    assert socket_mode == 0o660


def test_http_reservation_uses_api_key_attribution(tmp_path):
    state, secret, _other_secret = make_state(tmp_path)
    client = TestClient(build_http_app(state))

    response = client.post("/reservations", json={"device_id": "a4c91f2b"}, headers={"X-API-Key": secret})

    assert response.status_code == 200
    reservation = response.json()
    assert reservation["attribution"]["kind"] == "api_key"
    assert reservation["attribution"]["id"] == "autograder"


def test_http_rejects_reservation_without_api_key(tmp_path):
    state, _secret, _other_secret = make_state(tmp_path)
    client = TestClient(build_http_app(state))

    response = client.post("/reservations", json={"device_id": "a4c91f2b"})

    assert response.status_code == 401


def test_http_rejects_serial_read_without_api_key(tmp_path):
    state, secret, _other_secret = make_state(tmp_path)
    client = TestClient(build_http_app(state))
    opened = client.post(
        "/serial/sessions",
        json={"device_id": "a4c91f2b"},
        headers={"X-API-Key": secret},
    ).json()

    response = client.get(f"/serial/sessions/{opened['id']}/read")

    assert response.status_code == 401


def test_http_rejects_release_by_different_api_key(tmp_path):
    state, secret, other_secret = make_state(tmp_path)
    client = TestClient(build_http_app(state))
    reservation = client.post(
        "/reservations",
        json={"device_id": "a4c91f2b"},
        headers={"X-API-Key": secret},
    ).json()

    response = client.delete(f"/reservations/{reservation['id']}", headers={"X-API-Key": other_secret})

    assert response.status_code == 409
    assert [reservation.id for reservation in state.list_reservations()] == [reservation["id"]]


def test_serial_stream_moves_binary_frames(tmp_path):
    state, secret, _other_secret = make_state(tmp_path)
    client = TestClient(build_http_app(state))

    with client.websocket_connect("/serial/streams/a4c91f2b?baud_rate=230400", headers={"X-API-Key": secret}) as websocket:
        backend_session = state.serial.sessions[-1]
        backend_session.input_chunks.append(b"hello")
        assert websocket.receive_bytes() == b"hello"

        websocket.send_bytes(b"status")
        for _ in range(20):
            if backend_session.writes:
                break
            time.sleep(0.01)

        assert backend_session.writes == [b"status"]


def test_sdk_serial_stream_uses_live_websocket(tmp_path):
    state, secret, _other_secret = make_state(tmp_path)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = build_http_server(state, "127.0.0.1", port)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(50):
            if server.started:
                break
            time.sleep(0.01)

        client = SysbenchDevicesClient(base_url=f"http://127.0.0.1:{port}", api_key=secret)
        with client.serial_stream("a4c91f2b") as stream:
            backend_session = state.serial.sessions[-1]
            backend_session.input_chunks.append(b"pong")
            assert stream.read(timeout=1.0) == b"pong"
            stream.write(b"ping")

            for _ in range(20):
                if backend_session.writes:
                    break
                time.sleep(0.01)

            assert backend_session.writes == [b"ping"]
    finally:
        server.should_exit = True
        thread.join(timeout=2)


def test_only_one_serial_session_per_device(tmp_path):
    state, secret, _other_secret = make_state(tmp_path)
    attr = state.attribution_for_api_key(secret)
    session = state.open_serial("a4c91f2b", attribution=attr)
    try:
        with pytest.raises(ConflictError, match="already has an open serial session"):
            state.open_serial("a4c91f2b", attribution=attr)
    finally:
        state.close_serial(session.id, attribution=attr)


def test_socket_admin_can_release_api_key_reservation(tmp_path):
    state, secret, _other_secret = make_state(tmp_path)
    reservation = state.reserve(state.attribution_for_api_key(secret), device_id="a4c91f2b")

    dispatch_socket_method(state, "release", {"reservation_id": reservation.id})

    assert state.list_reservations() == ()
