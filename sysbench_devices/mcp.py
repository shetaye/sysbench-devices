"""MCP server backed by the public SDK."""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP

from sysbench_devices.client import SysbenchDevicesClient
from sysbench_devices.logging_config import configure_logging
from sysbench_devices.protocols.cs140e_bootloader import ARM_BASE
from sysbench_devices.state import decode_bytes, encode_bytes

logger = logging.getLogger(__name__)


@dataclass
class _MCPSerialStream:
    context: Any
    stream: Any


class MCPService:
    """Thin tool implementation layer for tests and FastMCP wrappers."""

    def __init__(self, client: SysbenchDevicesClient) -> None:
        self.client = client
        self._serial_streams: dict[str, _MCPSerialStream] = {}

    async def list_devices(self) -> dict[str, Any]:
        return await self.client.devices()

    async def list_reservations(self) -> list[dict[str, Any]]:
        return await self.client.reservations()

    async def reserve(self, device_id: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        return await self.client.reserve(device_id=device_id, tags=tags or [])

    async def release(self, reservation_id: str) -> dict[str, str]:
        await self.client.release(reservation_id)
        return {"reservation_id": reservation_id}

    async def power(self, device_id: str, action: str) -> dict[str, Any]:
        return await self.client.power(device_id, action)

    async def open_serial(self, device_id: str, baud_rate: int = 115200) -> dict[str, Any]:
        session = await self.client.open_serial(device_id, baud_rate=baud_rate)
        try:
            context = self.client.serial_stream(device_id)
            stream = await context.__aenter__()
        except BaseException:
            await self.client.close_serial(device_id)
            raise
        self._serial_streams[device_id] = _MCPSerialStream(context=context, stream=stream)
        return session

    async def read_serial(self, device_id: str, max_bytes: int = 4096) -> dict[str, str]:
        data = await self._serial_stream(device_id).read(max_bytes=max_bytes)
        return encode_bytes(data)

    async def write_serial(self, device_id: str, data: str, encoding: str = "utf-8") -> dict[str, int]:
        payload = decode_bytes(data, encoding)
        await self._serial_stream(device_id).write(payload)
        return {"bytes_written": len(payload)}

    async def close_serial(self, device_id: str) -> dict[str, str]:
        entry = self._serial_streams.pop(device_id, None)
        try:
            if entry is not None:
                await entry.context.__aexit__(None, None, None)
        finally:
            await self.client.close_serial(device_id)
        return {"device_id": device_id}

    async def bootload_file(
        self,
        device_id: str,
        binary_path: str,
        arm_base: int = ARM_BASE,
        max_output_bytes: int = 4096,
    ) -> dict[str, Any]:
        return await self.client.bootload_file(
            stream=self._serial_stream(device_id),
            path=binary_path,
            arm_base=arm_base,
            max_output_bytes=max_output_bytes,
        )

    def _serial_stream(self, device_id: str) -> Any:
        try:
            return self._serial_streams[device_id].stream
        except KeyError as exc:
            raise RuntimeError(f"serial stream is not open for device: {device_id}") from exc


def build_mcp_server(service: MCPService) -> FastMCP:
    mcp = FastMCP(
        "Sysbench Device Manager",
        instructions="Use the daemon API through SDK-backed MCP tools. Management-only operations are not exposed.",
    )

    @mcp.tool()
    async def list_devices() -> dict[str, Any]:
        """List registered and discovered sysbench devices."""
        return await service.list_devices()

    @mcp.tool()
    async def list_reservations() -> list[dict[str, Any]]:
        """List active device reservations."""
        return await service.list_reservations()

    @mcp.tool()
    async def reserve(device_id: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        """Reserve a device by ID or by tags."""
        return await service.reserve(device_id=device_id, tags=tags)

    @mcp.tool()
    async def release(reservation_id: str) -> dict[str, str]:
        """Release a reservation."""
        return await service.release(reservation_id)

    @mcp.tool()
    async def power(device_id: str, action: str) -> dict[str, Any]:
        """Run a power action: on, off, or cycle."""
        return await service.power(device_id, action)

    @mcp.tool()
    async def open_serial(device_id: str, baud_rate: int = 115200) -> dict[str, Any]:
        """Open a serial session."""
        return await service.open_serial(device_id, baud_rate=baud_rate)

    @mcp.tool()
    async def read_serial(device_id: str, max_bytes: int = 4096) -> dict[str, str]:
        """Read bytes from a serial session."""
        return await service.read_serial(device_id, max_bytes=max_bytes)

    @mcp.tool()
    async def write_serial(device_id: str, data: str, encoding: str = "utf-8") -> dict[str, int]:
        """Write bytes to a serial session."""
        return await service.write_serial(device_id, data, encoding=encoding)

    @mcp.tool()
    async def close_serial(device_id: str) -> dict[str, str]:
        """Close a serial session."""
        return await service.close_serial(device_id)

    @mcp.tool()
    async def bootload_file(
        device_id: str,
        binary_path: str,
        arm_base: int = ARM_BASE,
        max_output_bytes: int = 4096,
    ) -> dict[str, Any]:
        """Upload a CS140E bootloader binary file over an open serial session."""
        return await service.bootload_file(
            device_id=device_id,
            binary_path=binary_path,
            arm_base=arm_base,
            max_output_bytes=max_output_bytes,
        )

    return mcp


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
