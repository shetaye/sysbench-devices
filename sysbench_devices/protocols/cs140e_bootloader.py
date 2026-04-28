"""CS140E bootloader protocol over the public serial stream API."""

from __future__ import annotations

import base64
import struct
import time
from binascii import crc32
from dataclasses import dataclass
from typing import Any, Callable, Protocol

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


class BootloaderError(SysbenchDevicesError):
    """CS140E bootloader protocol failure."""

    code = "bootloader_error"
    http_status = 502


class BootloaderReadTimeout(BootloaderError):
    """Timed out while waiting for bootloader bytes."""


class BootloaderStream(Protocol):
    """Minimal byte stream required by the bootloader protocol."""

    def read(self, max_bytes: int = CHUNK_SIZE, timeout: float = 0.1) -> bytes: ...

    def write(self, data: bytes) -> None: ...


class SerialStreamClient(Protocol):
    def serial_stream(self, device_id: str, baud_rate: int = 115200, connect_timeout: float = 10.0) -> Any: ...


@dataclass(frozen=True)
class BootloadResult:
    session_id: str | None
    bytes_sent: int
    crc32: int
    arm_base: int
    prints: tuple[str, ...] = ()
    captured_output: bytes = b""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
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

    def get_u8(self, deadline: float, timeout_message: str) -> int:
        while not self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BootloaderReadTimeout(timeout_message)
            try:
                data = self._stream.read(max_bytes=CHUNK_SIZE, timeout=min(0.25, remaining))
            except BootloaderReadTimeout:
                continue
            except Exception as exc:
                raise BootloaderError(f"bootloader read failed: {exc}") from exc
            if data:
                self._buffer.extend(data)

        byte = self._buffer[0]
        del self._buffer[0]
        return byte

    def get_u32_raw(self, deadline: float, timeout_message: str) -> int:
        b0 = self.get_u8(deadline, timeout_message)
        b1 = self.get_u8(deadline, timeout_message)
        b2 = self.get_u8(deadline, timeout_message)
        b3 = self.get_u8(deadline, timeout_message)
        return b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)

    def get_op(self, deadline: float, timeout_message: str, on_print: Callable[[str], None] | None) -> int:
        while True:
            op = self.get_u32_raw(deadline, timeout_message)
            if op != PRINT_STRING:
                return op

            message = bytearray()
            while True:
                byte = self.get_u8(deadline, timeout_message)
                if byte == 0:
                    break
                message.append(byte)
            if on_print is not None:
                on_print(message.decode("utf-8", errors="replace"))

    def put_u32(self, value: int) -> None:
        self.put_bytes(struct.pack("<I", value))

    def put_bytes(self, data: bytes) -> None:
        try:
            self._stream.write(data)
        except Exception as exc:
            raise BootloaderError(f"bootloader write failed: {exc}") from exc


def bootload(
    stream: BootloaderStream,
    code: bytes,
    timeout: float = 10.0,
    arm_base: int = ARM_BASE,
    on_print: Callable[[str], None] | None = None,
) -> BootloadResult:
    """Upload a binary to a CS140E bootloader stream."""

    code_crc = crc32(code) & 0xFFFFFFFF
    prints: list[str] = []

    def capture_print(message: str) -> None:
        prints.append(message)
        if on_print is not None:
            on_print(message)

    wire = _BootloaderWire(stream)
    target = struct.pack("<I", GET_PROG_INFO)
    window = bytearray()
    deadline = time.monotonic() + timeout

    while True:
        byte = wire.get_u8(deadline, "timeout waiting for GET_PROG_INFO")
        window.append(byte)
        if len(window) > len(target):
            del window[0]
        if bytes(window) == target:
            break

    wire.put_u32(PUT_PROG_INFO)
    wire.put_u32(arm_base)
    wire.put_u32(len(code))
    wire.put_u32(code_crc)

    deadline = time.monotonic() + timeout
    while True:
        op = wire.get_op(deadline, "timeout waiting for GET_CODE", capture_print)
        if op == BOOT_ERROR:
            raise BootloaderError("pi rejected program info")
        if op != GET_PROG_INFO:
            break

    if op != GET_CODE:
        raise BootloaderError(f"expected GET_CODE (0x{GET_CODE:08X}), got 0x{op:08X}")

    echoed_crc = wire.get_u32_raw(deadline, "timeout waiting for CRC echo")
    if echoed_crc != code_crc:
        raise BootloaderError(f"CRC mismatch: sent 0x{code_crc:08X}, pi echoed 0x{echoed_crc:08X}")

    wire.put_u32(PUT_CODE)
    for offset in range(0, len(code), CHUNK_SIZE):
        wire.put_bytes(code[offset : offset + CHUNK_SIZE])

    deadline = time.monotonic() + timeout
    op = wire.get_op(deadline, "timeout waiting for BOOT_SUCCESS", capture_print)
    if op == BOOT_ERROR:
        raise BootloaderError("pi reported BOOT_ERROR after code transfer")
    if op != BOOT_SUCCESS:
        raise BootloaderError(f"expected BOOT_SUCCESS (0x{BOOT_SUCCESS:08X}), got 0x{op:08X}")

    return BootloadResult(
        session_id=getattr(stream, "session_id", None),
        bytes_sent=len(code),
        crc32=code_crc,
        arm_base=arm_base,
        prints=tuple(prints),
    )


def bootload_via_sdk(
    client: SerialStreamClient,
    device_id: str,
    payload: bytes,
    baud_rate: int = 115200,
    timeout: float = 10.0,
    arm_base: int = ARM_BASE,
    capture_output_seconds: float = 0.0,
    max_output_bytes: int = CHUNK_SIZE,
) -> BootloadResult:
    with client.serial_stream(device_id, baud_rate=baud_rate, connect_timeout=timeout) as stream:
        result = bootload(stream, payload, timeout=timeout, arm_base=arm_base)
        captured_output = _capture_output(stream, capture_output_seconds, max_output_bytes)
        return BootloadResult(
            session_id=getattr(stream, "session_id", None),
            bytes_sent=result.bytes_sent,
            crc32=result.crc32,
            arm_base=result.arm_base,
            prints=result.prints,
            captured_output=captured_output,
        )


def upload_binary(
    client: SerialStreamClient,
    device_id: str,
    payload: bytes,
    baud_rate: int = 115200,
) -> BootloadResult:
    return bootload_via_sdk(client=client, device_id=device_id, payload=payload, baud_rate=baud_rate)


def _capture_output(stream: BootloaderStream, seconds: float, max_bytes: int) -> bytes:
    if seconds <= 0 or max_bytes <= 0:
        return b""

    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + seconds
    while total < max_bytes:
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            break
        chunk = stream.read(max_bytes=min(CHUNK_SIZE, max_bytes - total), timeout=min(0.1, remaining_time))
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)
