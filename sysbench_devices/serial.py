"""Serial session backends."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from sysbench_devices.errors import HardwareError, ValidationError
from sysbench_devices.models import RuntimeDevice


class SerialPortSession(Protocol):
    def read(self, max_bytes: int, timeout: float) -> bytes:
        ...

    def write(self, data: bytes) -> int:
        ...

    def close(self) -> None:
        ...


class SerialBackend(Protocol):
    def open(self, runtime: RuntimeDevice, baud_rate: int) -> SerialPortSession:
        ...


@dataclass
class MemorySerialSession:
    input_chunks: deque[bytes] = field(default_factory=deque)
    writes: list[bytes] = field(default_factory=list)
    closed: bool = False

    def read(self, max_bytes: int, timeout: float) -> bytes:
        _validate_max_bytes(max_bytes)
        if timeout > 0:
            deadline = time.monotonic() + timeout
            while not self.input_chunks and time.monotonic() < deadline:
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        if not self.input_chunks:
            return b""
        chunk = self.input_chunks.popleft()
        if len(chunk) > max_bytes:
            self.input_chunks.appendleft(chunk[max_bytes:])
            return chunk[:max_bytes]
        return chunk

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.closed = True


class MemorySerialBackend:
    def __init__(self) -> None:
        self.sessions: list[MemorySerialSession] = []

    def open(self, runtime: RuntimeDevice, baud_rate: int) -> MemorySerialSession:
        session = MemorySerialSession()
        self.sessions.append(session)
        return session


class PySerialBackend:
    def open(self, runtime: RuntimeDevice, baud_rate: int) -> SerialPortSession:
        if runtime.serial_port is None:
            raise ValidationError(f"device {runtime.id} has no serial port")
        try:
            import serial as pyserial
        except ImportError as exc:
            raise HardwareError("pyserial is not installed") from exc
        return pyserial.Serial(runtime.serial_port, baudrate=baud_rate, timeout=0)


def read_until_quiet(session: SerialPortSession, max_bytes: int, quiet_time: float, timeout: float) -> bytes:
    _validate_max_bytes(max_bytes)
    deadline = time.monotonic() + timeout
    quiet_deadline = time.monotonic() + quiet_time
    chunks: list[bytes] = []
    total = 0
    while time.monotonic() < deadline and time.monotonic() < quiet_deadline and total < max_bytes:
        chunk = session.read(max_bytes - total, timeout=0.02)
        if chunk:
            chunks.append(chunk)
            total += len(chunk)
            quiet_deadline = time.monotonic() + quiet_time
    return b"".join(chunks)


def _validate_max_bytes(max_bytes: int) -> None:
    if max_bytes < 1:
        raise ValidationError("max_bytes must be positive")
