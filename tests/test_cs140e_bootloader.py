from __future__ import annotations

import base64
import struct
from binascii import crc32
from collections import deque
from typing import Callable

import pytest

from sysbench_devices.protocols.cs140e_bootloader import (
    ARM_BASE,
    BOOT_ERROR,
    BOOT_SUCCESS,
    GET_CODE,
    GET_PROG_INFO,
    PRINT_STRING,
    PUT_CODE,
    PUT_PROG_INFO,
    BootloaderError,
    bootload,
    bootload_via_sdk,
)


class MockStream:
    def __init__(self, handler: Callable[[MockStream], None]) -> None:
        self._recv_queue: deque[bytes] = deque()
        self._sent = bytearray()
        handler(self)

    def enqueue(self, data: bytes) -> None:
        self._recv_queue.append(data)

    def enqueue_u32(self, value: int) -> None:
        self.enqueue(struct.pack("<I", value))

    def enqueue_string(self, value: str) -> None:
        self.enqueue_u32(PRINT_STRING)
        self.enqueue(value.encode() + b"\0")

    def read(self, max_bytes: int = 4096, timeout: float = 0.1) -> bytes:
        if not self._recv_queue:
            return b""
        chunk = self._recv_queue.popleft()
        if len(chunk) > max_bytes:
            self._recv_queue.appendleft(chunk[max_bytes:])
            return chunk[:max_bytes]
        return chunk

    def write(self, data: bytes) -> None:
        self._sent.extend(data)

    @property
    def sent_bytes(self) -> bytes:
        return bytes(self._sent)


def _standard_pi(code: bytes) -> Callable[[MockStream], None]:
    code_crc = crc32(code) & 0xFFFFFFFF

    def handler(mock: MockStream) -> None:
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_CODE)
        mock.enqueue_u32(code_crc)
        mock.enqueue_u32(BOOT_SUCCESS)

    return handler


def test_bootloader_success() -> None:
    code = b"test binary payload"
    mock = MockStream(_standard_pi(code))

    result = bootload(mock, code, timeout=1.0)

    expected = bytearray()
    expected.extend(struct.pack("<I", PUT_PROG_INFO))
    expected.extend(struct.pack("<I", ARM_BASE))
    expected.extend(struct.pack("<I", len(code)))
    expected.extend(struct.pack("<I", crc32(code) & 0xFFFFFFFF))
    expected.extend(struct.pack("<I", PUT_CODE))
    expected.extend(code)
    assert mock.sent_bytes == bytes(expected)
    assert result.bytes_sent == len(code)
    assert result.crc32 == crc32(code) & 0xFFFFFFFF
    assert result.session_id is None


def test_bootloader_with_prints() -> None:
    code = b"another binary"
    code_crc = crc32(code) & 0xFFFFFFFF

    def handler(mock: MockStream) -> None:
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_string("checking info")
        mock.enqueue_u32(GET_CODE)
        mock.enqueue_u32(code_crc)
        mock.enqueue_string("code ok")
        mock.enqueue_u32(BOOT_SUCCESS)

    mock = MockStream(handler)
    prints: list[str] = []
    result = bootload(mock, code, timeout=1.0, on_print=prints.append)

    assert prints == ["checking info", "code ok"]
    assert result.prints == ("checking info", "code ok")


def test_bootloader_boot_error() -> None:
    code = b"some code"

    def handler(mock: MockStream) -> None:
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(BOOT_ERROR)

    with pytest.raises(BootloaderError, match="rejected"):
        bootload(MockStream(handler), code, timeout=1.0)


def test_bootloader_crc_mismatch() -> None:
    code = b"some code"
    wrong_crc = ((crc32(code) & 0xFFFFFFFF) + 1) & 0xFFFFFFFF

    def handler(mock: MockStream) -> None:
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_CODE)
        mock.enqueue_u32(wrong_crc)

    with pytest.raises(BootloaderError, match="CRC mismatch"):
        bootload(MockStream(handler), code, timeout=1.0)


def test_bootloader_garbage_before_get_prog_info() -> None:
    code = b"test"
    code_crc = crc32(code) & 0xFFFFFFFF

    def handler(mock: MockStream) -> None:
        mock.enqueue(b"\xDE\xAD\xBE\xEF\x00\x01\x02")
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_CODE)
        mock.enqueue_u32(code_crc)
        mock.enqueue_u32(BOOT_SUCCESS)

    result = bootload(MockStream(handler), code, timeout=1.0)

    assert result.bytes_sent == len(code)


def test_bootloader_drains_extra_get_prog_info() -> None:
    code = b"test"
    code_crc = crc32(code) & 0xFFFFFFFF

    def handler(mock: MockStream) -> None:
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_CODE)
        mock.enqueue_u32(code_crc)
        mock.enqueue_u32(BOOT_SUCCESS)

    result = bootload(MockStream(handler), code, timeout=1.0)

    assert result.bytes_sent == len(code)


class FakeSDKClient:
    def __init__(self, code: bytes) -> None:
        code_crc = crc32(code) & 0xFFFFFFFF
        self.reads: deque[bytes] = deque(
            [
                struct.pack("<I", GET_PROG_INFO),
                struct.pack("<I", GET_CODE),
                struct.pack("<I", code_crc),
                struct.pack("<I", BOOT_SUCCESS),
            ]
        )
        self.sent = bytearray()
        self.calls: list[tuple] = []

    def open_serial(self, device_id: str, baud_rate: int = 115200) -> dict[str, object]:
        self.calls.append(("open_serial", device_id, baud_rate))
        return {"id": "session-1"}

    def read_serial(self, session_id: str, max_bytes: int = 4096, timeout: float = 0.1) -> dict[str, str]:
        self.calls.append(("read_serial", session_id, max_bytes, timeout))
        chunk = self.reads.popleft() if self.reads else b""
        return {"encoding": "base64", "data": base64.b64encode(chunk).decode("ascii")}

    def write_serial(self, session_id: str, data: str, encoding: str = "utf-8") -> dict[str, int]:
        self.calls.append(("write_serial", session_id, data, encoding))
        assert encoding == "base64"
        raw = base64.b64decode(data.encode("ascii"))
        self.sent.extend(raw)
        return {"bytes_written": len(raw)}

    def close_serial(self, session_id: str) -> None:
        self.calls.append(("close_serial", session_id))


def test_bootload_via_sdk_uses_http_serial_session() -> None:
    code = b"program"
    client = FakeSDKClient(code)

    result = bootload_via_sdk(client, "board-1", code, baud_rate=230400, timeout=1.0)

    assert result.session_id == "session-1"
    assert result.bytes_sent == len(code)
    assert client.calls[0] == ("open_serial", "board-1", 230400)
    assert client.calls[-1] == ("close_serial", "session-1")
    assert client.sent.endswith(struct.pack("<I", PUT_CODE) + code)
