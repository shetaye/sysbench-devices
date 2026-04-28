from __future__ import annotations

import struct
from binascii import crc32
from collections import deque
from typing import Callable

import anyio
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
    bootload_stream,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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

    async def read(self, max_bytes: int = 4096) -> bytes:
        if not self._recv_queue:
            return b""
        chunk = self._recv_queue.popleft()
        if len(chunk) > max_bytes:
            self._recv_queue.appendleft(chunk[max_bytes:])
            return chunk[:max_bytes]
        return chunk

    async def write(self, data: bytes) -> None:
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


@pytest.mark.anyio
async def test_bootloader_success() -> None:
    code = b"test binary payload"
    mock = MockStream(_standard_pi(code))

    result = await bootload(mock, code)

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
    assert result.device_id is None


@pytest.mark.anyio
async def test_bootloader_with_prints() -> None:
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
    result = await bootload(mock, code, on_print=prints.append)

    assert prints == ["checking info", "code ok"]
    assert result.prints == ("checking info", "code ok")


@pytest.mark.anyio
async def test_bootloader_boot_error() -> None:
    code = b"some code"

    def handler(mock: MockStream) -> None:
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(BOOT_ERROR)

    with pytest.raises(BootloaderError, match="rejected"):
        await bootload(MockStream(handler), code)


@pytest.mark.anyio
async def test_bootloader_crc_mismatch() -> None:
    code = b"some code"
    wrong_crc = ((crc32(code) & 0xFFFFFFFF) + 1) & 0xFFFFFFFF

    def handler(mock: MockStream) -> None:
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_CODE)
        mock.enqueue_u32(wrong_crc)

    with pytest.raises(BootloaderError, match="CRC mismatch"):
        await bootload(MockStream(handler), code)


@pytest.mark.anyio
async def test_bootloader_garbage_before_get_prog_info() -> None:
    code = b"test"
    code_crc = crc32(code) & 0xFFFFFFFF

    def handler(mock: MockStream) -> None:
        mock.enqueue(b"\xDE\xAD\xBE\xEF\x00\x01\x02")
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_CODE)
        mock.enqueue_u32(code_crc)
        mock.enqueue_u32(BOOT_SUCCESS)

    result = await bootload(MockStream(handler), code)

    assert result.bytes_sent == len(code)


@pytest.mark.anyio
async def test_bootloader_drains_extra_get_prog_info() -> None:
    code = b"test"
    code_crc = crc32(code) & 0xFFFFFFFF

    def handler(mock: MockStream) -> None:
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_PROG_INFO)
        mock.enqueue_u32(GET_CODE)
        mock.enqueue_u32(code_crc)
        mock.enqueue_u32(BOOT_SUCCESS)

    result = await bootload(MockStream(handler), code)

    assert result.bytes_sent == len(code)


@pytest.mark.anyio
async def test_bootload_stream_uses_open_stream() -> None:
    code = b"program"
    stream = MockStream(_standard_pi(code))
    stream.device_id = "board-1"

    result = await bootload_stream(stream, code)

    assert result.device_id == "board-1"
    assert result.bytes_sent == len(code)
    assert stream.sent_bytes.endswith(struct.pack("<I", PUT_CODE) + code)


@pytest.mark.anyio
async def test_bootloader_uses_caller_deadline() -> None:
    stream = MockStream(lambda _mock: None)

    with pytest.raises(TimeoutError):
        with anyio.fail_after(0.01):
            await bootload(stream, b"program")
