"""MCP server backed by the public SDK."""

from __future__ import annotations

import argparse
import base64
import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from sysbench_devices.client import SysbenchDevicesClient
from sysbench_devices.logging_config import configure_logging
from sysbench_devices.protocols.cs140e_bootloader import ARM_BASE

logger = logging.getLogger(__name__)


class MCPService:
    """Thin tool implementation layer for tests and FastMCP wrappers."""

    def __init__(self, client: SysbenchDevicesClient) -> None:
        self.client = client

    def list_devices(self) -> dict[str, Any]:
        return self.client.devices()

    def list_reservations(self) -> list[dict[str, Any]]:
        return self.client.reservations()

    def reserve(self, device_id: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        return self.client.reserve(device_id=device_id, tags=tags or [])

    def release(self, reservation_id: str) -> dict[str, str]:
        self.client.release(reservation_id)
        return {"reservation_id": reservation_id}

    def power(self, device_id: str, action: str) -> dict[str, Any]:
        return self.client.power(device_id, action)

    def serial_open(self, device_id: str, baud_rate: int = 115200) -> dict[str, Any]:
        return self.client.open_serial(device_id, baud_rate=baud_rate)

    def serial_read(self, session_id: str, max_bytes: int = 4096, timeout: float = 0.1) -> dict[str, str]:
        return self.client.read_serial(session_id, max_bytes=max_bytes, timeout=timeout)

    def serial_write(self, session_id: str, data: str, encoding: str = "utf-8") -> dict[str, int]:
        return self.client.write_serial(session_id, data, encoding=encoding)

    def serial_close(self, session_id: str) -> dict[str, str]:
        self.client.close_serial(session_id)
        return {"session_id": session_id}

    def serial_run(
        self,
        device_id: str,
        data: str,
        encoding: str = "utf-8",
        baud_rate: int = 115200,
        append_newline: bool = True,
        max_bytes: int = 4096,
    ) -> dict[str, str]:
        return self.client.run_serial(
            device_id,
            data,
            encoding=encoding,
            baud_rate=baud_rate,
            append_newline=append_newline,
            max_bytes=max_bytes,
        )

    def bootload_binary(
        self,
        device_id: str,
        data: str,
        encoding: str = "base64",
        baud_rate: int = 115200,
        timeout: float = 10.0,
        arm_base: int = ARM_BASE,
        capture_output_seconds: float = 0.0,
        max_output_bytes: int = 4096,
    ) -> dict[str, Any]:
        payload = _decode_tool_payload(data, encoding)
        return self.client.bootload(
            device_id=device_id,
            payload=payload,
            baud_rate=baud_rate,
            timeout=timeout,
            arm_base=arm_base,
            capture_output_seconds=capture_output_seconds,
            max_output_bytes=max_output_bytes,
        )


def build_mcp_server(service: MCPService) -> FastMCP:
    mcp = FastMCP(
        "Sysbench Device Manager",
        instructions="Use the daemon API through SDK-backed MCP tools. Management-only operations are not exposed.",
    )

    @mcp.tool()
    def list_devices() -> dict[str, Any]:
        """List registered and discovered sysbench devices."""
        return service.list_devices()

    @mcp.tool()
    def list_reservations() -> list[dict[str, Any]]:
        """List active device reservations."""
        return service.list_reservations()

    @mcp.tool()
    def reserve(device_id: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        """Reserve a device by ID or by tags."""
        return service.reserve(device_id=device_id, tags=tags)

    @mcp.tool()
    def release(reservation_id: str) -> dict[str, str]:
        """Release a reservation."""
        return service.release(reservation_id)

    @mcp.tool()
    def power(device_id: str, action: str) -> dict[str, Any]:
        """Run a power action: on, off, or cycle."""
        return service.power(device_id, action)

    @mcp.tool()
    def serial_open(device_id: str, baud_rate: int = 115200) -> dict[str, Any]:
        """Open a serial session."""
        return service.serial_open(device_id, baud_rate=baud_rate)

    @mcp.tool()
    def serial_read(session_id: str, max_bytes: int = 4096, timeout: float = 0.1) -> dict[str, str]:
        """Read bytes from a serial session."""
        return service.serial_read(session_id, max_bytes=max_bytes, timeout=timeout)

    @mcp.tool()
    def serial_write(session_id: str, data: str, encoding: str = "utf-8") -> dict[str, int]:
        """Write bytes to a serial session."""
        return service.serial_write(session_id, data, encoding=encoding)

    @mcp.tool()
    def serial_close(session_id: str) -> dict[str, str]:
        """Close a serial session."""
        return service.serial_close(session_id)

    @mcp.tool()
    def serial_run(
        device_id: str,
        data: str,
        encoding: str = "utf-8",
        baud_rate: int = 115200,
        append_newline: bool = True,
        max_bytes: int = 4096,
    ) -> dict[str, str]:
        """Open serial, write data, read until quiet, and close the session."""
        return service.serial_run(
            device_id=device_id,
            data=data,
            encoding=encoding,
            baud_rate=baud_rate,
            append_newline=append_newline,
            max_bytes=max_bytes,
        )

    @mcp.tool()
    def bootload_binary(
        device_id: str,
        data: str,
        encoding: str = "base64",
        baud_rate: int = 115200,
        timeout: float = 10.0,
        arm_base: int = ARM_BASE,
        capture_output_seconds: float = 0.0,
        max_output_bytes: int = 4096,
    ) -> dict[str, Any]:
        """Upload a CS140E bootloader binary over a WebSocket serial stream."""
        return service.bootload_binary(
            device_id=device_id,
            data=data,
            encoding=encoding,
            baud_rate=baud_rate,
            timeout=timeout,
            arm_base=arm_base,
            capture_output_seconds=capture_output_seconds,
            max_output_bytes=max_output_bytes,
        )

    return mcp


def _decode_tool_payload(data: str, encoding: str) -> bytes:
    if encoding == "base64":
        return base64.b64decode(data.encode("ascii"))
    if encoding == "hex":
        return bytes.fromhex(data)
    if encoding == "utf-8":
        return data.encode("utf-8")
    raise ValueError(f"unsupported payload encoding: {encoding}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sbdevmcp")
    parser.add_argument("--base-url", default=os.environ.get("SBDEVD_HTTP_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--api-key", default=os.environ.get("SBDEVD_API_KEY"))
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default=os.environ.get("SBDEVMCP_TRANSPORT", "stdio"))
    parser.add_argument("--log-level", default=os.environ.get("SBDEVMCP_LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    logger.info("starting sbdevmcp base_url=%s transport=%s", args.base_url, args.transport)
    service = MCPService(SysbenchDevicesClient(base_url=args.base_url, api_key=args.api_key))
    server = build_mcp_server(service)
    server.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
