"""CS140E bootloader protocol over the public serial stream API."""

from __future__ import annotations

import base64
import inspect
import struct
from binascii import crc32
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

import anyio

from sysbench_devices.errors import SysbenchDevicesError


GET_PROG_INFO = 0x11112222
PUT_PROG_INFO = 0x33334444
GET_CODE = 0x55556666
PUT_CODE = 0x77778888
BOOT_SUCCESS = 0x9999AAAA
BOOT_ERROR = 0xBBBBCCCC
PRINT_STRING = 0xDDDDEEEE

ARM_BASE = 0x8000
CHUNK_SIZE = 4096

PrintCallback = Callable[[str], object | Awaitable[object]]


class BootloaderError(SysbenchDevicesError):
    """CS140E bootloader protocol failure."""

    code = "bootloader_error"
    http_status = 502


class BootloaderStream(Protocol):
    """Minimal byte stream required by the bootloader protocol."""

    async def read(self, max_bytes: int = CHUNK_SIZE) -> bytes: ...

    async def write(self, data: bytes) -> None: ...


@dataclass(frozen=True)
class BootloadResult:
    device_id: str | None
    bytes_sent: int
    crc32: int
    arm_base: int
    prints: tuple[str, ...] = ()
    captured_output: bytes = b""

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "bytes_sent": self.bytes_sent,
            "crc32": self.crc32,
            "arm_base": self.arm_base,
            "prints": list(self.prints),
            "captured_output": {
                "encoding": "base64",
                "data": base64.b64encode(self.captured_output).decode("ascii"),
            },
        }


class _BootloaderWire:
    def __init__(self, stream: BootloaderStream) -> None:
        self._stream = stream
        self._buffer = bytearray()

    async def get_u8(self) -> int:
        while not self._buffer:
            try:
                data = await self._stream.read(max_bytes=CHUNK_SIZE)
            except Exception as exc:
                raise BootloaderError(f"bootloader read failed: {exc}") from exc
            if data:
                self._buffer.extend(data)
            else:
                await anyio.lowlevel.checkpoint()

        byte = self._buffer[0]
        del self._buffer[0]
        return byte

    async def get_u32_raw(self) -> int:
        b0 = await self.get_u8()
        b1 = await self.get_u8()
        b2 = await self.get_u8()
        b3 = await self.get_u8()
        return b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)

    async def get_op(self, on_print: PrintCallback | None) -> int:
        while True:
            op = await self.get_u32_raw()
            if op != PRINT_STRING:
                return op

            message = bytearray()
            while True:
                byte = await self.get_u8()
                if byte == 0:
                    break
                message.append(byte)
            if on_print is not None:
                result = on_print(message.decode("utf-8", errors="replace"))
                if inspect.isawaitable(result):
                    await result

    async def put_u32(self, value: int) -> None:
        await self.put_bytes(struct.pack("<I", value))

    async def put_bytes(self, data: bytes) -> None:
        try:
            await self._stream.write(data)
        except Exception as exc:
            raise BootloaderError(f"bootloader write failed: {exc}") from exc


async def bootload(
    stream: BootloaderStream,
    code: bytes,
    arm_base: int = ARM_BASE,
    on_print: PrintCallback | None = None,
) -> BootloadResult:
    """Upload a binary to a CS140E bootloader stream."""

    code_crc = crc32(code) & 0xFFFFFFFF
    prints: list[str] = []

    async def capture_print(message: str) -> None:
        prints.append(message)
        if on_print is not None:
            result = on_print(message)
            if inspect.isawaitable(result):
                await result

    wire = _BootloaderWire(stream)
    await _wait_for_get_prog_info(wire)

    await _send_program_info(wire, arm_base, len(code), code_crc)

    op = await _wait_for_code_request(wire, capture_print)
    if op != GET_CODE:
        raise BootloaderError(f"expected GET_CODE (0x{GET_CODE:08X}), got 0x{op:08X}")

    echoed_crc = await wire.get_u32_raw()
    if echoed_crc != code_crc:
        raise BootloaderError(f"CRC mismatch: sent 0x{code_crc:08X}, pi echoed 0x{echoed_crc:08X}")

    await wire.put_u32(PUT_CODE)
    for offset in range(0, len(code), CHUNK_SIZE):
        await wire.put_bytes(code[offset : offset + CHUNK_SIZE])

    op = await wire.get_op(capture_print)
    if op == BOOT_ERROR:
        raise BootloaderError("pi reported BOOT_ERROR after code transfer")
    if op != BOOT_SUCCESS:
        raise BootloaderError(f"expected BOOT_SUCCESS (0x{BOOT_SUCCESS:08X}), got 0x{op:08X}")

    return BootloadResult(
        device_id=getattr(stream, "device_id", None),
        bytes_sent=len(code),
        crc32=code_crc,
        arm_base=arm_base,
        prints=tuple(prints),
    )


async def bootload_stream(
    stream: BootloaderStream,
    payload: bytes,
    arm_base: int = ARM_BASE,
    max_output_bytes: int = CHUNK_SIZE,
) -> BootloadResult:
    result = await bootload(stream, payload, arm_base=arm_base)
    captured_output = await _capture_output(stream, max_output_bytes)
    return BootloadResult(
        device_id=getattr(stream, "device_id", None),
        bytes_sent=result.bytes_sent,
        crc32=result.crc32,
        arm_base=result.arm_base,
        prints=result.prints,
        captured_output=captured_output,
    )


async def bootload_file(
    stream: BootloaderStream,
    path: str | Path,
    arm_base: int = ARM_BASE,
    max_output_bytes: int = CHUNK_SIZE,
) -> BootloadResult:
    return await bootload_stream(
        stream=stream,
        payload=await anyio.Path(path).read_bytes(),
        arm_base=arm_base,
        max_output_bytes=max_output_bytes,
    )


async def _wait_for_get_prog_info(wire: _BootloaderWire) -> None:
    target = struct.pack("<I", GET_PROG_INFO)
    window = bytearray()

    while True:
        byte = await wire.get_u8()
        window.append(byte)
        if len(window) > len(target):
            del window[0]
        if bytes(window) == target:
            return


async def _send_program_info(wire: _BootloaderWire, arm_base: int, code_size: int, code_crc: int) -> None:
    await wire.put_u32(PUT_PROG_INFO)
    await wire.put_u32(arm_base)
    await wire.put_u32(code_size)
    await wire.put_u32(code_crc)


async def _wait_for_code_request(wire: _BootloaderWire, on_print: PrintCallback | None) -> int:
    while True:
        op = await wire.get_op(on_print)
        if op == BOOT_ERROR:
            raise BootloaderError("pi rejected program info")
        if op != GET_PROG_INFO:
            return op


async def _capture_output(stream: BootloaderStream, max_bytes: int) -> bytes:
    if max_bytes <= 0:
        return b""

    chunks: list[bytes] = []
    total = 0
    while total < max_bytes:
        chunk = await stream.read(max_bytes=min(CHUNK_SIZE, max_bytes - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)
