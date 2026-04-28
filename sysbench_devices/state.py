"""Daemon state and orchestration."""

from __future__ import annotations

import base64
import secrets
import threading
from dataclasses import dataclass
from typing import Iterable

from sysbench_devices.api_keys import create_api_key, resolve_attribution
from sysbench_devices.discovery import DiscoveryBackend, EmptyDiscoveryBackend
from sysbench_devices.errors import AuthenticationError, ConflictError, NotFoundError, ValidationError
from sysbench_devices.models import (
    ApiKeyRecord,
    Device,
    DeviceRegistration,
    DeviceState,
    DeviceView,
    DiscoveredDevice,
    PowerAction,
    Reservation,
    ReservationAttribution,
    RuntimeDevice,
    SerialSession,
)
from sysbench_devices.power import NoopPowerBackend, PowerBackend
from sysbench_devices.registry import RegistryData, RegistryStore
from sysbench_devices.serial import MemorySerialBackend, SerialBackend, SerialPortSession, read_until_quiet


@dataclass
class _OpenSerialSession:
    public: SerialSession
    backend: SerialPortSession


class DeviceStateStore:
    def __init__(
        self,
        registry: RegistryStore,
        discovery: DiscoveryBackend | None = None,
        power: PowerBackend | None = None,
        serial: SerialBackend | None = None,
    ) -> None:
        self.registry = registry
        self.discovery = discovery if discovery is not None else EmptyDiscoveryBackend()
        self.power = power if power is not None else NoopPowerBackend()
        self.serial = serial if serial is not None else MemorySerialBackend()
        self._lock = threading.RLock()
        self._reservations: dict[str, Reservation] = {}
        self._serial_sessions: dict[str, _OpenSerialSession] = {}
        self._last_runtime_by_id: dict[str, RuntimeDevice] = {}

    def list_devices(self) -> DeviceView:
        with self._lock:
            return self._reconcile()

    def discover(self) -> tuple[DiscoveredDevice, ...]:
        with self._lock:
            return self._reconcile().discovered

    def register(self, device_id: str, name: str | None = None, tags: Iterable[str] = ()) -> DeviceRegistration:
        with self._lock:
            runtime_by_id = self._discover_runtime_by_id()
            if device_id not in runtime_by_id:
                raise NotFoundError(f"discovered device not found: {device_id}")
            data = self.registry.load()
            if device_id in data.device_by_id():
                raise ConflictError(f"device already registered: {device_id}")
            registration = DeviceRegistration(id=device_id, name=name, tags=tuple(tags))
            self.registry.save(RegistryData(devices=(*data.devices, registration), api_keys=data.api_keys))
            return registration

    def update(self, device_id: str, name: str | None = None, tags: Iterable[str] | None = None) -> DeviceRegistration:
        with self._lock:
            data = self.registry.load()
            updated: list[DeviceRegistration] = []
            found = False
            for device in data.devices:
                if device.id == device_id:
                    found = True
                    updated.append(
                        DeviceRegistration(
                            id=device.id,
                            name=device.name if name is None else name,
                            tags=device.tags if tags is None else tuple(tags),
                        )
                    )
                else:
                    updated.append(device)
            if not found:
                raise NotFoundError(f"registered device not found: {device_id}")
            self.registry.save(RegistryData(devices=tuple(updated), api_keys=data.api_keys))
            return next(device for device in updated if device.id == device_id)

    def delete(self, device_id: str) -> None:
        with self._lock:
            data = self.registry.load()
            devices = tuple(device for device in data.devices if device.id != device_id)
            if len(devices) == len(data.devices):
                raise NotFoundError(f"registered device not found: {device_id}")
            self.registry.save(RegistryData(devices=devices, api_keys=data.api_keys))
            for reservation_id, reservation in list(self._reservations.items()):
                if reservation.device_id == device_id:
                    del self._reservations[reservation_id]

    def list_api_keys(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(key.to_dict(include_hash=False) for key in self.registry.load().api_keys)

    def create_api_key(self, key_id: str, label: str) -> dict[str, object]:
        with self._lock:
            data = self.registry.load()
            if key_id in data.api_key_by_id():
                raise ConflictError(f"api key already exists: {key_id}")
            created = create_api_key(key_id, label)
            self.registry.save(RegistryData(devices=data.devices, api_keys=(*data.api_keys, created.record)))
            return {"api_key": created.record.to_dict(include_hash=False), "secret": created.secret}

    def revoke_api_key(self, key_id: str) -> None:
        with self._lock:
            data = self.registry.load()
            keys: list[ApiKeyRecord] = []
            found = False
            for key in data.api_keys:
                if key.id == key_id:
                    found = True
                    keys.append(ApiKeyRecord(id=key.id, label=key.label, key_hash=key.key_hash, revoked=True))
                else:
                    keys.append(key)
            if not found:
                raise NotFoundError(f"api key not found: {key_id}")
            self.registry.save(RegistryData(devices=data.devices, api_keys=tuple(keys)))

    def attribution_for_api_key(self, secret: str | None) -> ReservationAttribution:
        if not secret:
            raise AuthenticationError("missing API key")
        with self._lock:
            return resolve_attribution(secret, self.registry.load().api_keys)

    def reserve(
        self,
        attribution: ReservationAttribution,
        device_id: str | None = None,
        tags: Iterable[str] = (),
    ) -> Reservation:
        with self._lock:
            view = self._reconcile()
            selected_device_id = device_id or self._select_device_by_tags(view, tuple(tags))
            if selected_device_id is None:
                raise ValidationError("device_id or at least one tag is required")
            if self._reservation_for_device(selected_device_id) is not None:
                raise ConflictError(f"device is already reserved: {selected_device_id}")
            if not any(device.id == selected_device_id and device.state == DeviceState.ONLINE for device in view.devices):
                raise NotFoundError(f"online registered device not found: {selected_device_id}")
            reservation = Reservation(
                id=_short_id(),
                device_id=selected_device_id,
                attribution=attribution,
            )
            self._reservations[reservation.id] = reservation
            return reservation

    def list_reservations(self) -> tuple[Reservation, ...]:
        with self._lock:
            return tuple(sorted(self._reservations.values(), key=lambda item: item.id))

    def release_reservation(
        self,
        reservation_id: str,
        attribution: ReservationAttribution | None = None,
    ) -> None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise NotFoundError(f"reservation not found: {reservation_id}")
            if attribution is not None and not _can_manage_reservation(attribution, reservation):
                raise ConflictError("reservation belongs to a different attribution")
            del self._reservations[reservation_id]

    def power_device(
        self,
        device_id: str,
        action: PowerAction | str,
        attribution: ReservationAttribution | None = None,
    ) -> dict[str, object]:
        with self._lock:
            runtime = self._runtime_for_device(device_id)
            self._ensure_device_access(device_id, attribution)
            return self.power.set_power(runtime, PowerAction(action))

    def open_serial(
        self,
        device_id: str,
        baud_rate: int = 115200,
        attribution: ReservationAttribution | None = None,
    ) -> SerialSession:
        with self._lock:
            runtime = self._runtime_for_device(device_id)
            self._ensure_device_access(device_id, attribution)
            backend_session = self.serial.open(runtime, baud_rate)
            public = SerialSession(
                id=_short_id(),
                device_id=device_id,
                baud_rate=baud_rate,
                attribution=attribution,
            )
            self._serial_sessions[public.id] = _OpenSerialSession(public=public, backend=backend_session)
            return public

    def read_serial(
        self,
        session_id: str,
        max_bytes: int = 4096,
        timeout: float = 0.1,
        attribution: ReservationAttribution | None = None,
    ) -> bytes:
        with self._lock:
            session = self._serial_session(session_id)
            self._ensure_serial_session_access(session, attribution)
            return session.backend.read(max_bytes=max_bytes, timeout=timeout)

    def write_serial(
        self,
        session_id: str,
        data: bytes,
        attribution: ReservationAttribution | None = None,
    ) -> int:
        with self._lock:
            session = self._serial_session(session_id)
            self._ensure_serial_session_access(session, attribution)
            return session.backend.write(data)

    def close_serial(
        self,
        session_id: str,
        attribution: ReservationAttribution | None = None,
    ) -> None:
        with self._lock:
            session = self._serial_session(session_id)
            self._ensure_serial_session_access(session, attribution)
            session = self._serial_sessions.pop(session_id, None)
            if session is None:
                raise NotFoundError(f"serial session not found: {session_id}")
            session.backend.close()

    def run_serial_command(
        self,
        device_id: str,
        payload: bytes,
        baud_rate: int = 115200,
        append_newline: bool = True,
        max_bytes: int = 4096,
        quiet_time: float = 0.05,
        timeout: float = 1.0,
        attribution: ReservationAttribution | None = None,
    ) -> bytes:
        session = self.open_serial(device_id=device_id, baud_rate=baud_rate, attribution=attribution)
        try:
            data = payload + (b"\n" if append_newline else b"")
            self.write_serial(session.id, data)
            return read_until_quiet(self._serial_session(session.id).backend, max_bytes, quiet_time, timeout)
        finally:
            self.close_serial(session.id)

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "devices": len(self.registry.load().devices),
                "reservations": len(self._reservations),
                "serial_sessions": len(self._serial_sessions),
            }

    def _select_device_by_tags(self, view: DeviceView, tags: tuple[str, ...]) -> str | None:
        if not tags:
            return None
        requested = set(tags)
        for device in view.devices:
            if device.state == DeviceState.ONLINE and requested.issubset(device.tags):
                return device.id
        raise NotFoundError(f"no online registered device matches tags: {', '.join(tags)}")

    def _runtime_for_device(self, device_id: str) -> RuntimeDevice:
        runtime_by_id = self._discover_runtime_by_id()
        runtime = runtime_by_id.get(device_id)
        if runtime is None:
            raise NotFoundError(f"online device not found: {device_id}")
        if device_id not in self.registry.load().device_by_id():
            raise NotFoundError(f"registered device not found: {device_id}")
        return runtime

    def _ensure_device_access(self, device_id: str, attribution: ReservationAttribution | None) -> None:
        reservation = self._reservation_for_device(device_id)
        if reservation is None or attribution is None:
            return
        if not _can_manage_reservation(attribution, reservation):
            raise ConflictError("device is reserved by a different attribution")

    def _reservation_for_device(self, device_id: str) -> Reservation | None:
        for reservation in self._reservations.values():
            if reservation.device_id == device_id:
                return reservation
        return None

    def _serial_session(self, session_id: str) -> _OpenSerialSession:
        session = self._serial_sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"serial session not found: {session_id}")
        return session

    def _ensure_serial_session_access(
        self,
        session: _OpenSerialSession,
        attribution: ReservationAttribution | None,
    ) -> None:
        if attribution is None or session.public.attribution is None:
            return
        reservation = Reservation(
            id=session.public.id,
            device_id=session.public.device_id,
            attribution=session.public.attribution,
        )
        if not _can_manage_reservation(attribution, reservation):
            raise ConflictError("serial session belongs to a different attribution")

    def _reconcile(self) -> DeviceView:
        data = self.registry.load()
        runtime_by_id = self._discover_runtime_by_id()
        devices: list[Device] = []
        for registration in sorted(data.devices, key=lambda item: item.id):
            runtime = runtime_by_id.get(registration.id)
            reservation = self._reservation_for_device(registration.id)
            if runtime is None:
                state = DeviceState.OFFLINE
            elif reservation is not None:
                state = DeviceState.RESERVED
            else:
                state = DeviceState.ONLINE
            devices.append(
                Device(
                    id=registration.id,
                    name=registration.name,
                    tags=registration.tags,
                    state=state,
                    runtime=runtime,
                    reservation=reservation,
                )
            )
        registered_ids = {device.id for device in data.devices}
        discovered = tuple(
            DiscoveredDevice(id=runtime.id, runtime=runtime)
            for runtime in sorted(runtime_by_id.values(), key=lambda item: item.id)
            if runtime.id not in registered_ids
        )
        return DeviceView(devices=tuple(devices), discovered=discovered)

    def _discover_runtime_by_id(self) -> dict[str, RuntimeDevice]:
        runtimes = self.discovery.discover()
        runtime_by_id: dict[str, RuntimeDevice] = {}
        for runtime in runtimes:
            if runtime.id in runtime_by_id:
                raise ConflictError(f"duplicate runtime device id: {runtime.id}")
            runtime_by_id[runtime.id] = runtime
        self._last_runtime_by_id = runtime_by_id
        return runtime_by_id


def admin_attribution() -> ReservationAttribution:
    return ReservationAttribution.admin()


def encode_bytes(data: bytes) -> dict[str, str]:
    return {"encoding": "base64", "data": base64.b64encode(data).decode("ascii")}


def decode_bytes(data: str, encoding: str = "utf-8") -> bytes:
    if encoding == "utf-8":
        return data.encode("utf-8")
    if encoding == "base64":
        return base64.b64decode(data.encode("ascii"))
    if encoding == "hex":
        return bytes.fromhex(data)
    raise ValidationError(f"unsupported encoding: {encoding}")


def _short_id() -> str:
    return secrets.token_hex(4)


def _can_manage_reservation(attribution: ReservationAttribution, reservation: Reservation) -> bool:
    if attribution.kind == "socket" and attribution.id == "admin":
        return True
    return (
        attribution.kind == reservation.attribution.kind
        and attribution.id == reservation.attribution.id
    )
