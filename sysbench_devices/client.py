"""Python SDK over the public HTTP/WebSocket API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from websocket import WebSocketConnectionClosedException, WebSocketTimeoutException, create_connection


class SysbenchDevicesClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def devices(self) -> dict[str, Any]:
        return self._request("GET", "/devices")

    def reservations(self) -> list[dict[str, Any]]:
        return self._request("GET", "/reservations")

    def reserve(self, device_id: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        return self._request("POST", "/reservations", {"device_id": device_id, "tags": tags or []})

    def release(self, reservation_id: str) -> None:
        self._request("DELETE", f"/reservations/{reservation_id}")

    def power(self, device_id: str, action: str) -> dict[str, Any]:
        return self._request("POST", f"/devices/{device_id}/power", {"action": action})

    def open_serial(self, device_id: str, baud_rate: int = 115200) -> dict[str, Any]:
        return self._request("POST", f"/devices/{quote(device_id, safe='')}/serial", {"baud_rate": baud_rate})

    def close_serial(self, device_id: str) -> None:
        self._request("DELETE", f"/devices/{quote(device_id, safe='')}/serial")

    def serial_stream(
        self,
        device_id: str,
        connect_timeout: float = 10.0,
    ) -> "SerialStream":
        return SerialStream(
            device_id=device_id,
            url=self._websocket_url(f"/devices/{quote(device_id, safe='')}/serial/stream"),
            headers=self._websocket_headers(),
            connect_timeout=connect_timeout,
        )

    def bootload(
        self,
        stream: Any,
        payload: bytes,
        timeout: float = 10.0,
        arm_base: int = 0x8000,
        capture_output_seconds: float = 0.0,
        max_output_bytes: int = 4096,
    ) -> dict[str, Any]:
        from sysbench_devices.protocols.cs140e_bootloader import bootload_stream

        return bootload_stream(
            stream=stream,
            payload=payload,
            timeout=timeout,
            arm_base=arm_base,
            capture_output_seconds=capture_output_seconds,
            max_output_bytes=max_output_bytes,
        ).to_dict()

    def bootload_file(
        self,
        stream: Any,
        path: str | Path,
        timeout: float = 10.0,
        arm_base: int = 0x8000,
        capture_output_seconds: float = 0.0,
        max_output_bytes: int = 4096,
    ) -> dict[str, Any]:
        return self.bootload(
            stream=stream,
            payload=Path(path).read_bytes(),
            timeout=timeout,
            arm_base=arm_base,
            capture_output_seconds=capture_output_seconds,
            max_output_bytes=max_output_bytes,
        )

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {"Accept": "application/json"}
        data = None
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read()
        except HTTPError as exc:
            raw_error = exc.read().decode("utf-8")
            try:
                parsed = json.loads(raw_error)
                error = parsed.get("error", {})
                raise RuntimeError(f"{error.get('code')}: {error.get('message')}") from exc
            except json.JSONDecodeError:
                raise RuntimeError(raw_error) from exc
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def _websocket_url(self, path: str) -> str:
        parsed = urlsplit(self.base_url)
        scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
        base_path = parsed.path.rstrip("/")
        return urlunsplit((scheme, parsed.netloc, f"{base_path}{path}", "", ""))

    def _websocket_headers(self) -> list[str]:
        if self.api_key is None:
            return []
        return [f"X-API-Key: {self.api_key}"]


class SerialStream:
    """Blocking binary WebSocket stream compatible with bootloader byte I/O."""

    def __init__(self, device_id: str, url: str, headers: list[str], connect_timeout: float) -> None:
        self.device_id = device_id
        self.url = url
        self.headers = headers
        self.connect_timeout = connect_timeout
        self._socket: Any | None = None
        self._buffer = bytearray()

    def __enter__(self) -> "SerialStream":
        self._socket = create_connection(self.url, header=self.headers, timeout=self.connect_timeout)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def read(self, max_bytes: int = 4096, timeout: float = 0.1) -> bytes:
        if self._socket is None:
            raise RuntimeError("serial stream is not open")
        if self._buffer:
            return self._drain_buffer(max_bytes)
        self._socket.settimeout(timeout)
        try:
            frame = self._socket.recv()
        except WebSocketTimeoutException:
            return b""
        except WebSocketConnectionClosedException:
            return b""
        if isinstance(frame, str):
            raise RuntimeError("serial stream received a text frame")
        self._buffer.extend(frame)
        return self._drain_buffer(max_bytes)

    def write(self, data: bytes) -> None:
        if self._socket is None:
            raise RuntimeError("serial stream is not open")
        self._socket.send_binary(data)

    def close(self) -> None:
        if self._socket is None:
            return
        self._socket.close()
        self._socket = None

    def _drain_buffer(self, max_bytes: int) -> bytes:
        chunk = bytes(self._buffer[:max_bytes])
        del self._buffer[:max_bytes]
        return chunk
