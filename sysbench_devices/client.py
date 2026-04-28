"""Python SDK over the public HTTP/WebSocket API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import anyio
import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


HTTP_REQUEST_DEADLINE_SECONDS = 10.0
WEBSOCKET_CONNECT_DEADLINE_SECONDS = 10.0
WEBSOCKET_WRITE_DEADLINE_SECONDS = 10.0
SERIAL_READ_IDLE_SECONDS = 0.1


class SysbenchDevicesClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def devices(self) -> dict[str, Any]:
        return await self._request("GET", "/devices")

    async def reservations(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/reservations")

    async def reserve(self, device_id: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        return await self._request("POST", "/reservations", {"device_id": device_id, "tags": tags or []})

    async def release(self, reservation_id: str) -> None:
        await self._request("DELETE", f"/reservations/{reservation_id}")

    async def power(self, device_id: str, action: str) -> dict[str, Any]:
        return await self._request("POST", f"/devices/{device_id}/power", {"action": action})

    async def open_serial(self, device_id: str, baud_rate: int = 115200) -> dict[str, Any]:
        return await self._request("POST", f"/devices/{quote(device_id, safe='')}/serial", {"baud_rate": baud_rate})

    async def close_serial(self, device_id: str) -> None:
        await self._request("DELETE", f"/devices/{quote(device_id, safe='')}/serial")

    def serial_stream(
        self,
        device_id: str,
    ) -> "SerialStream":
        return SerialStream(
            device_id=device_id,
            url=self._websocket_url(f"/devices/{quote(device_id, safe='')}/serial/stream"),
            headers=self._websocket_headers(),
        )

    async def bootload(
        self,
        stream: Any,
        payload: bytes,
        arm_base: int = 0x8000,
        max_output_bytes: int = 4096,
    ) -> dict[str, Any]:
        from sysbench_devices.protocols.cs140e_bootloader import bootload_stream

        return (
            await bootload_stream(
                stream=stream,
                payload=payload,
                arm_base=arm_base,
                max_output_bytes=max_output_bytes,
            )
        ).to_dict()

    async def bootload_file(
        self,
        stream: Any,
        path: str | Path,
        arm_base: int = 0x8000,
        max_output_bytes: int = 4096,
    ) -> dict[str, Any]:
        return await self.bootload(
            stream=stream,
            payload=await anyio.Path(path).read_bytes(),
            arm_base=arm_base,
            max_output_bytes=max_output_bytes,
        )

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            with anyio.fail_after(HTTP_REQUEST_DEADLINE_SECONDS):
                async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
                    response = await client.request(
                        method,
                        f"{self.base_url}{path}",
                        headers=headers,
                        json=body,
                    )
        except TimeoutError as exc:
            raise RuntimeError(f"HTTP request timed out: {method} {path}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"HTTP request failed: {exc}") from exc
        if response.status_code >= 400:
            self._raise_http_error(response)
        if not response.content:
            return None
        return response.json()

    def _raise_http_error(self, response: httpx.Response) -> None:
        try:
            parsed = response.json()
            error = parsed.get("error", {})
            raise RuntimeError(f"{error.get('code')}: {error.get('message')}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(response.text) from exc

    def _websocket_url(self, path: str) -> str:
        parsed = urlsplit(self.base_url)
        scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
        base_path = parsed.path.rstrip("/")
        return urlunsplit((scheme, parsed.netloc, f"{base_path}{path}", "", ""))

    def _websocket_headers(self) -> dict[str, str]:
        if self.api_key is None:
            return {}
        return {"X-API-Key": self.api_key}


class SerialStream:
    """Async binary WebSocket stream compatible with bootloader byte I/O."""

    def __init__(self, device_id: str, url: str, headers: dict[str, str]) -> None:
        self.device_id = device_id
        self.url = url
        self.headers = headers
        self._socket: Any | None = None
        self._buffer = bytearray()

    async def __aenter__(self) -> "SerialStream":
        try:
            with anyio.fail_after(WEBSOCKET_CONNECT_DEADLINE_SECONDS):
                self._socket = await connect(
                    self.url,
                    additional_headers=self.headers,
                    open_timeout=None,
                    proxy=None,
                )
        except TimeoutError as exc:
            raise RuntimeError(f"serial stream connection timed out: {self.device_id}") from exc
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def read(self, max_bytes: int = 4096) -> bytes:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if self._socket is None:
            raise RuntimeError("serial stream is not open")
        if self._buffer:
            return self._drain_buffer(max_bytes)
        try:
            with anyio.move_on_after(SERIAL_READ_IDLE_SECONDS) as scope:
                frame = await self._socket.recv(decode=False)
            if scope.cancelled_caught:
                return b""
        except ConnectionClosed:
            return b""
        if not isinstance(frame, bytes | bytearray | memoryview):
            raise RuntimeError("serial stream received a text frame")
        self._buffer.extend(frame)
        return self._drain_buffer(max_bytes)

    async def write(self, data: bytes) -> None:
        if self._socket is None:
            raise RuntimeError("serial stream is not open")
        with anyio.fail_after(WEBSOCKET_WRITE_DEADLINE_SECONDS):
            await self._socket.send(data)

    async def close(self) -> None:
        if self._socket is None:
            return
        socket = self._socket
        self._socket = None
        await socket.close()

    def _drain_buffer(self, max_bytes: int) -> bytes:
        chunk = bytes(self._buffer[:max_bytes])
        del self._buffer[:max_bytes]
        return chunk
