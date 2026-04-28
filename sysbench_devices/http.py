"""HTTP API for automation clients."""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from sysbench_devices.errors import SysbenchDevicesError, ValidationError, error_response
from sysbench_devices.models import PowerAction, ReservationAttribution
from sysbench_devices.state import DeviceStateStore, decode_bytes, encode_bytes

JSON = dict[str, Any]
logger = logging.getLogger(__name__)


class SysbenchHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], state: DeviceStateStore) -> None:
        self.state = state
        super().__init__(server_address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: SysbenchHTTPServer

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("http access: " + format, *args)

    def _handle(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            path = [part for part in parsed.path.split("/") if part]
            query = parse_qs(parsed.query)
            body = self._read_json_body()
            result = dispatch_http_method(
                self.server.state,
                method=method,
                path=path,
                query=query,
                body=body,
                api_key=self._api_key(),
            )
            self._write_json(HTTPStatus.OK, result)
            logger.debug("HTTP %s %s ok", method, self.path)
        except BaseException as exc:
            status = exc.http_status if isinstance(exc, SysbenchDevicesError) else 500
            logger.warning("HTTP %s %s failed status=%s error=%s", method, self.path, status, exc)
            self._write_json(HTTPStatus(status), {"error": error_response(exc)})

    def _api_key(self) -> str | None:
        explicit = self.headers.get("X-API-Key")
        if explicit:
            return explicit
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def _read_json_body(self) -> JSON:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValidationError("request body must be an object")
        return data

    def _write_json(self, status: HTTPStatus, data: Any) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def dispatch_http_method(
    state: DeviceStateStore,
    method: str,
    path: list[str],
    query: dict[str, list[str]],
    body: JSON,
    api_key: str | None,
) -> Any:
    if method == "GET" and path == ["devices"]:
        return state.list_devices().to_dict()
    if method == "GET" and path == ["reservations"]:
        return [reservation.to_dict() for reservation in state.list_reservations()]

    if method == "POST" and path == ["reservations"]:
        attr = _require_attribution(state, api_key)
        reservation = state.reserve(
            attribution=attr,
            device_id=body.get("device_id"),
            tags=body.get("tags", ()),
        )
        return reservation.to_dict()

    if method == "DELETE" and len(path) == 2 and path[0] == "reservations":
        attr = _require_attribution(state, api_key)
        state.release_reservation(path[1], attribution=attr)
        return None

    if method == "POST" and len(path) == 3 and path[0] == "devices" and path[2] == "power":
        attr = _require_attribution(state, api_key)
        return state.power_device(path[1], PowerAction(body["action"]), attribution=attr)

    if method == "POST" and path == ["serial", "sessions"]:
        attr = _require_attribution(state, api_key)
        return state.open_serial(
            device_id=str(body["device_id"]),
            baud_rate=int(body.get("baud_rate", 115200)),
            attribution=attr,
        ).to_dict()

    if method == "GET" and len(path) == 4 and path[:2] == ["serial", "sessions"] and path[3] == "read":
        attr = _require_attribution(state, api_key)
        data = state.read_serial(
            session_id=path[2],
            max_bytes=int(_first(query, "max_bytes", "4096")),
            timeout=float(_first(query, "timeout", "0.1")),
            attribution=attr,
        )
        return encode_bytes(data)

    if method == "POST" and len(path) == 4 and path[:2] == ["serial", "sessions"] and path[3] == "write":
        attr = _require_attribution(state, api_key)
        payload = decode_bytes(str(body.get("data", "")), str(body.get("encoding", "utf-8")))
        return {"bytes_written": state.write_serial(path[2], payload, attribution=attr)}

    if method == "DELETE" and len(path) == 3 and path[:2] == ["serial", "sessions"]:
        attr = _require_attribution(state, api_key)
        state.close_serial(path[2], attribution=attr)
        return None

    if method == "POST" and path == ["serial", "run"]:
        attr = _require_attribution(state, api_key)
        payload = decode_bytes(str(body.get("data", "")), str(body.get("encoding", "utf-8")))
        data = state.run_serial_command(
            device_id=str(body["device_id"]),
            payload=payload,
            baud_rate=int(body.get("baud_rate", 115200)),
            append_newline=bool(body.get("append_newline", True)),
            max_bytes=int(body.get("max_bytes", 4096)),
            attribution=attr,
        )
        return encode_bytes(data)

    raise ValidationError(f"unknown HTTP route: {method} /{'/'.join(path)}")


def _require_attribution(state: DeviceStateStore, api_key: str | None) -> ReservationAttribution:
    return state.attribution_for_api_key(api_key)


def _first(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    return values[0] if values else default
