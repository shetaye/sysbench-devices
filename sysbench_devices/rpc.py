"""Newline-delimited JSON RPC over a local Unix socket."""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sysbench_devices.errors import SysbenchDevicesError, ValidationError, error_response
from sysbench_devices.models import PowerAction
from sysbench_devices.state import DeviceStateStore, admin_attribution

JSON = dict[str, Any]
logger = logging.getLogger(__name__)
STREAM_BYTES = 4096
STREAM_READ_TIMEOUT = 0.05


class SocketRPCConnectionError(RuntimeError):
    """Unix socket connection failure."""


class SocketRPCServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        state: DeviceStateStore,
        doctor: Callable[[], Any] | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.state = state
        self.doctor = doctor
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            logger.warning("removing stale socket path %s", self.socket_path)
            self.socket_path.unlink()
        super().__init__(str(self.socket_path), _SocketRPCHandler)
        self.socket_path.chmod(0o660)
        logger.info("socket RPC listening on %s", self.socket_path)

    def server_close(self) -> None:
        super().server_close()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


class _SocketRPCHandler(socketserver.StreamRequestHandler):
    server: SocketRPCServer

    def handle(self) -> None:
        for raw_line in self.rfile:
            try:
                request = json.loads(raw_line.decode("utf-8"))
                method = str(request["method"])
                params = request.get("params", {})
                if not isinstance(params, dict):
                    raise ValidationError("params must be an object")
                if method == "serial.stream":
                    self._handle_serial_stream(params)
                    return
                result = dispatch_socket_method(self.server.state, method, params, self.server.doctor)
                logger.debug("socket RPC method=%s ok", method)
                response = {"ok": True, "result": result}
            except BaseException as exc:
                logger.exception("socket RPC request failed")
                response = {"ok": False, "error": error_response(exc)}
            self._write_response(response)

    def _handle_serial_stream(self, params: JSON) -> None:
        attr = admin_attribution()
        device_id = str(params["device_id"])
        self.server.state.get_serial_session(device_id, attr)
        self._write_response({"ok": True, "result": {"device_id": device_id}})

        stop = threading.Event()
        reader = threading.Thread(
            target=_serial_to_socket,
            args=(self.server.state, self.request, device_id, stop),
            daemon=True,
        )
        reader.start()
        try:
            while not stop.is_set():
                data = self.request.recv(STREAM_BYTES)
                if not data:
                    return
                self.server.state.write_serial(device_id, data, attr)
        finally:
            stop.set()
            reader.join(timeout=1)

    def _write_response(self, response: JSON) -> None:
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")
        self.wfile.flush()


class SocketRPCClient:
    def __init__(self, socket_path: str | os.PathLike[str]) -> None:
        self.socket_path = str(socket_path)

    def call(self, method: str, **params: Any) -> Any:
        logger.debug("socket RPC client call method=%s", method)
        with self._connect() as client:
            file = client.makefile("rwb")
            file.write(json.dumps({"method": method, "params": params}).encode("utf-8") + b"\n")
            file.flush()
            raw = file.readline()
        if not raw:
            raise RuntimeError("empty RPC response")
        response = json.loads(raw.decode("utf-8"))
        if response.get("ok"):
            return response["result"]
        error = response.get("error", {})
        raise RuntimeError(f"{error.get('code', 'error')}: {error.get('message', '')}")

    def stream_serial(self, device_id: str, input_stream: Any, output_stream: Any) -> None:
        logger.debug("socket RPC client serial stream device_id=%s", device_id)
        with self._connect() as client:
            client.sendall(
                json.dumps({"method": "serial.stream", "params": {"device_id": device_id}}).encode("utf-8") + b"\n"
            )
            response = _read_response(client)
            if not response.get("ok"):
                error = response.get("error", {})
                raise RuntimeError(f"{error.get('code', 'error')}: {error.get('message', '')}")

            stop = threading.Event()
            reader = threading.Thread(target=_socket_to_output, args=(client, output_stream, stop), daemon=True)
            reader.start()
            try:
                while not stop.is_set():
                    chunk = input_stream.read(STREAM_BYTES)
                    if not chunk:
                        return
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    client.sendall(chunk)
            finally:
                stop.set()
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                reader.join(timeout=1)

    def _connect(self) -> socket.socket:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(self.socket_path)
        except FileNotFoundError as exc:
            client.close()
            raise SocketRPCConnectionError(f"socket not found: {self.socket_path}") from exc
        except PermissionError as exc:
            client.close()
            raise SocketRPCConnectionError(f"permission denied for socket: {self.socket_path}") from exc
        except ConnectionRefusedError as exc:
            client.close()
            raise SocketRPCConnectionError(f"socket is not accepting connections: {self.socket_path}") from exc
        except OSError as exc:
            client.close()
            raise SocketRPCConnectionError(f"could not connect to socket {self.socket_path}: {exc}") from exc
        return client


def dispatch_socket_method(
    state: DeviceStateStore,
    method: str,
    params: JSON,
    doctor: Callable[[], Any] | None = None,
) -> Any:
    attr = admin_attribution()
    match method:
        case "devices" | "rescan":
            return state.list_devices().to_dict()
        case "discover":
            return [device.to_dict() for device in state.discover()]
        case "register":
            return state.register(
                device_id=str(params["device_id"]),
                name=params.get("name"),
                tags=params.get("tags", ()),
            ).to_dict()
        case "update":
            return state.update(
                device_id=str(params["device_id"]),
                name=params.get("name"),
                tags=params.get("tags"),
            ).to_dict()
        case "delete":
            state.delete(str(params["device_id"]))
            return None
        case "reservations":
            return [reservation.to_dict() for reservation in state.list_reservations()]
        case "reserve":
            return state.reserve(
                attribution=attr,
                device_id=params.get("device_id"),
                tags=params.get("tags", ()),
            ).to_dict()
        case "release":
            state.release_reservation(str(params["reservation_id"]), attribution=attr)
            return None
        case "power":
            return state.power_device(str(params["device_id"]), PowerAction(params["action"]), attribution=attr)
        case "serial.open":
            return state.open_serial(
                device_id=str(params["device_id"]),
                baud_rate=int(params.get("baud_rate", 115200)),
                attribution=attr,
            ).to_dict()
        case "serial.close":
            state.close_serial(str(params["device_id"]), attribution=attr)
            return None
        case "api_keys.list":
            return list(state.list_api_keys())
        case "api_keys.create":
            return state.create_api_key(str(params["key_id"]), str(params["label"]))
        case "api_keys.revoke":
            state.revoke_api_key(str(params["key_id"]))
            return None
        case "status":
            return state.status()
        case "doctor":
            if doctor is None:
                raise ValidationError("doctor is not configured")
            report = doctor()
            return report.to_dict()
        case _:
            raise ValidationError(f"unknown method: {method}")


def _serial_to_socket(state: DeviceStateStore, sock: socket.socket, device_id: str, stop: threading.Event) -> None:
    attr = admin_attribution()
    try:
        while not stop.is_set():
            data = state.read_serial(device_id, STREAM_BYTES, STREAM_READ_TIMEOUT, attr)
            if data:
                sock.sendall(data)
    except (OSError, SysbenchDevicesError):
        stop.set()


def _socket_to_output(sock: socket.socket, output_stream: Any, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            data = sock.recv(STREAM_BYTES)
            if not data:
                return
            output_stream.write(data)
            output_stream.flush()
    finally:
        stop.set()


def _read_response(sock: socket.socket) -> JSON:
    raw = bytearray()
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise RuntimeError("empty RPC response")
        if chunk == b"\n":
            break
        raw.extend(chunk)
    return json.loads(raw.decode("utf-8"))
