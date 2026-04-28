"""Daemon entrypoint."""

from __future__ import annotations

import argparse
import logging
import os
import threading
from pathlib import Path

from sysbench_devices.discovery import HostDiscoveryBackend
from sysbench_devices.doctor import run_doctor
from sysbench_devices.http import build_http_server
from sysbench_devices.logging_config import configure_logging
from sysbench_devices.power import UhubctlPowerBackend
from sysbench_devices.registry import RegistryStore
from sysbench_devices.rpc import SocketRPCServer
from sysbench_devices.serial import PySerialBackend
from sysbench_devices.state import DeviceStateStore

logger = logging.getLogger(__name__)


def default_socket_path() -> Path:
    override = os.environ.get("SBDEVD_SOCKET")
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "sysbench-devices" / "sbdevd.sock"
    return Path("/run/sysbench-devices/sbdevd.sock")


def default_registry_path() -> Path:
    override = os.environ.get("SBDEVD_REGISTRY")
    if override:
        return Path(override)
    return Path.home() / ".config" / "sysbench-devices" / "registry.toml"


def build_state(registry_path: str | os.PathLike[str]) -> DeviceStateStore:
    return DeviceStateStore(
        registry=RegistryStore(registry_path),
        discovery=HostDiscoveryBackend(),
        power=UhubctlPowerBackend(),
        serial=PySerialBackend(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sbdevd")
    parser.add_argument("--registry", default=str(default_registry_path()))
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--http-host", default=os.environ.get("SBDEVD_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--http-port", type=int, default=int(os.environ.get("SBDEVD_HTTP_PORT", "8765")))
    parser.add_argument("--log-level", default=os.environ.get("SBDEVD_LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    logger.info("starting sbdevd registry=%s socket=%s http=%s:%s", args.registry, args.socket, args.http_host, args.http_port)
    state = build_state(args.registry)

    def doctor() -> object:
        return run_doctor(args.registry, args.socket)

    socket_server = SocketRPCServer(args.socket, state, doctor=doctor)
    http_server = build_http_server(state, args.http_host, args.http_port)

    socket_thread = threading.Thread(target=socket_server.serve_forever, name="sbdevd-rpc", daemon=True)
    socket_thread.start()
    try:
        logger.info("sbdevd ready")
        http_server.run()
    finally:
        logger.info("stopping sbdevd")
        socket_server.shutdown()
        socket_server.server_close()
        socket_thread.join(timeout=2)
        logger.info("sbdevd stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
