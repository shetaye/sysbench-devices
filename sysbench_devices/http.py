"""HTTP and WebSocket API for automation clients."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from sysbench_devices.errors import (
    AuthenticationError,
    ConflictError,
    HardwareError,
    NotFoundError,
    SysbenchDevicesError,
    ValidationError,
    error_response,
)
from sysbench_devices.models import PowerAction, ReservationAttribution
from sysbench_devices.state import DeviceStateStore, decode_bytes, encode_bytes

JSON = dict[str, Any]
STREAM_READ_BYTES = 4096
STREAM_READ_TIMEOUT = 0.05


def build_http_app(state: DeviceStateStore) -> FastAPI:
    app = FastAPI(title="Sysbench Devices")

    @app.exception_handler(SysbenchDevicesError)
    async def domain_error(_request: Request, exc: SysbenchDevicesError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"error": error_response(exc)})

    @app.get("/devices")
    def devices() -> JSON:
        return state.list_devices().to_dict()

    @app.get("/reservations")
    def reservations() -> list[JSON]:
        return [reservation.to_dict() for reservation in state.list_reservations()]

    @app.post("/reservations")
    def reserve(body: JSON, request: Request) -> JSON:
        reservation = state.reserve(
            attribution=_require_attribution(state, request),
            device_id=body.get("device_id"),
            tags=body.get("tags", ()),
        )
        return reservation.to_dict()

    @app.delete("/reservations/{reservation_id}")
    def release(reservation_id: str, request: Request) -> None:
        state.release_reservation(reservation_id, attribution=_require_attribution(state, request))

    @app.post("/devices/{device_id}/power")
    def power(device_id: str, body: JSON, request: Request) -> JSON:
        return state.power_device(device_id, PowerAction(body["action"]), attribution=_require_attribution(state, request))

    @app.post("/serial/sessions")
    def open_serial(body: JSON, request: Request) -> JSON:
        return state.open_serial(
            device_id=str(body["device_id"]),
            baud_rate=int(body.get("baud_rate", 115200)),
            attribution=_require_attribution(state, request),
        ).to_dict()

    @app.get("/serial/sessions/{session_id}/read")
    def read_serial(session_id: str, request: Request, max_bytes: int = 4096, timeout: float = 0.1) -> JSON:
        data = state.read_serial(
            session_id=session_id,
            max_bytes=max_bytes,
            timeout=timeout,
            attribution=_require_attribution(state, request),
        )
        return encode_bytes(data)

    @app.post("/serial/sessions/{session_id}/write")
    def write_serial(session_id: str, body: JSON, request: Request) -> JSON:
        payload = decode_bytes(str(body.get("data", "")), str(body.get("encoding", "utf-8")))
        return {"bytes_written": state.write_serial(session_id, payload, attribution=_require_attribution(state, request))}

    @app.delete("/serial/sessions/{session_id}")
    def close_serial(session_id: str, request: Request) -> None:
        state.close_serial(session_id, attribution=_require_attribution(state, request))

    @app.post("/serial/run")
    def run_serial(body: JSON, request: Request) -> JSON:
        payload = decode_bytes(str(body.get("data", "")), str(body.get("encoding", "utf-8")))
        data = state.run_serial_command(
            device_id=str(body["device_id"]),
            payload=payload,
            baud_rate=int(body.get("baud_rate", 115200)),
            append_newline=bool(body.get("append_newline", True)),
            max_bytes=int(body.get("max_bytes", 4096)),
            attribution=_require_attribution(state, request),
        )
        return encode_bytes(data)

    @app.websocket("/serial/streams/{device_id}")
    async def serial_stream(websocket: WebSocket, device_id: str, baud_rate: int = 115200) -> None:
        try:
            attr = _websocket_attribution(state, websocket)
            session = await asyncio.to_thread(state.open_serial, device_id, baud_rate, attr)
        except SysbenchDevicesError as exc:
            await websocket.close(code=_websocket_close_code(exc), reason=str(exc)[:120])
            return

        await websocket.accept()
        tasks = {
            asyncio.create_task(_serial_to_websocket(state, websocket, session.id, attr)),
            asyncio.create_task(_websocket_to_serial(state, websocket, session.id, attr)),
        }
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        except WebSocketDisconnect:
            pass
        finally:
            with contextlib.suppress(SysbenchDevicesError):
                await asyncio.to_thread(state.close_serial, session.id, attr)

    return app


def build_http_server(state: DeviceStateStore, host: str, port: int) -> uvicorn.Server:
    return uvicorn.Server(
        uvicorn.Config(
            build_http_app(state),
            host=host,
            port=port,
            access_log=False,
            log_config=None,
            lifespan="off",
        )
    )


async def _serial_to_websocket(
    state: DeviceStateStore,
    websocket: WebSocket,
    session_id: str,
    attr: ReservationAttribution,
) -> None:
    while True:
        data = await asyncio.to_thread(
            _read_serial_stream_chunk,
            state,
            session_id,
            attr,
        )
        if data is None:
            return
        if data:
            await websocket.send_bytes(data)
        else:
            await asyncio.sleep(0)


async def _websocket_to_serial(
    state: DeviceStateStore,
    websocket: WebSocket,
    session_id: str,
    attr: ReservationAttribution,
) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        data = message.get("bytes")
        if data is None:
            await websocket.close(code=1003, reason="binary frames required")
            return
        await asyncio.to_thread(state.write_serial, session_id, data, attr)


def _read_serial_stream_chunk(
    state: DeviceStateStore,
    session_id: str,
    attr: ReservationAttribution,
) -> bytes | None:
    try:
        return state.read_serial(session_id, STREAM_READ_BYTES, STREAM_READ_TIMEOUT, attr)
    except NotFoundError:
        return None


def _require_attribution(state: DeviceStateStore, request: Request) -> ReservationAttribution:
    return state.attribution_for_api_key(_api_key(request.headers))


def _websocket_attribution(state: DeviceStateStore, websocket: WebSocket) -> ReservationAttribution:
    return state.attribution_for_api_key(_api_key(websocket.headers) or websocket.query_params.get("api_key"))


def _api_key(headers: Any) -> str | None:
    explicit = headers.get("X-API-Key")
    if explicit:
        return explicit
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _websocket_close_code(exc: SysbenchDevicesError) -> int:
    if isinstance(exc, AuthenticationError):
        return 4401
    if isinstance(exc, (ConflictError, NotFoundError, ValidationError)):
        return 4400
    if isinstance(exc, HardwareError):
        return 1011
    return 1011
