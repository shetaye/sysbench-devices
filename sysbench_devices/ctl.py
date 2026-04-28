"""Command line control tool for the Unix socket superset API."""

from __future__ import annotations

import argparse
import json
from typing import Any

from sysbench_devices.daemon import default_socket_path
from sysbench_devices.rpc import SocketRPCClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sbdevctl")
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--json", action="store_true", help="emit raw JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("registry")
    sub.add_parser("devices")
    sub.add_parser("discover")
    sub.add_parser("reservations")
    sub.add_parser("status")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--verbose", action="store_true")

    register = sub.add_parser("register")
    register.add_argument("device_id")
    register.add_argument("--name")
    register.add_argument("--tag", action="append", default=[])

    update = sub.add_parser("update")
    update.add_argument("device_id")
    update.add_argument("--name")
    update.add_argument("--tag", action="append")

    delete = sub.add_parser("delete")
    delete.add_argument("device_id")

    reserve = sub.add_parser("reserve")
    reserve.add_argument("device_id", nargs="?")
    reserve.add_argument("--tag", action="append", default=[])

    release = sub.add_parser("release")
    release.add_argument("reservation_id")

    power = sub.add_parser("power")
    power.add_argument("device_id")
    power.add_argument("action", choices=["on", "off", "cycle"])

    serial = sub.add_parser("serial")
    serial_sub = serial.add_subparsers(dest="serial_command", required=True)
    serial_open = serial_sub.add_parser("open")
    serial_open.add_argument("device_id")
    serial_open.add_argument("--baud-rate", type=int, default=115200)
    serial_read = serial_sub.add_parser("read")
    serial_read.add_argument("session_id")
    serial_read.add_argument("--max-bytes", type=int, default=4096)
    serial_read.add_argument("--timeout", type=float, default=0.1)
    serial_write = serial_sub.add_parser("write")
    serial_write.add_argument("session_id")
    serial_write.add_argument("data")
    serial_write.add_argument("--encoding", choices=["utf-8", "base64", "hex"], default="utf-8")
    serial_close = serial_sub.add_parser("close")
    serial_close.add_argument("session_id")
    serial_run = serial_sub.add_parser("run")
    serial_run.add_argument("device_id")
    serial_run.add_argument("data")
    serial_run.add_argument("--encoding", choices=["utf-8", "base64", "hex"], default="utf-8")
    serial_run.add_argument("--baud-rate", type=int, default=115200)
    serial_run.add_argument("--no-newline", action="store_true")
    serial_run.add_argument("--max-bytes", type=int, default=4096)

    api_keys = sub.add_parser("api-keys")
    api_sub = api_keys.add_subparsers(dest="api_key_command", required=True)
    api_sub.add_parser("list")
    api_create = api_sub.add_parser("create")
    api_create.add_argument("--id", required=True)
    api_create.add_argument("--label", required=True)
    api_revoke = api_sub.add_parser("revoke")
    api_revoke.add_argument("key_id")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    client = SocketRPCClient(args.socket)
    result = _dispatch(client, args)
    _print_result(result, args)
    return 0


def _dispatch(client: SocketRPCClient, args: argparse.Namespace) -> Any:
    match args.command:
        case "registry" | "devices":
            return client.call("devices")
        case "discover":
            return client.call("discover")
        case "register":
            return client.call("register", device_id=args.device_id, name=args.name, tags=args.tag)
        case "update":
            return client.call("update", device_id=args.device_id, name=args.name, tags=args.tag)
        case "delete":
            client.call("delete", device_id=args.device_id)
            return {"device_id": args.device_id}
        case "reserve":
            return client.call("reserve", device_id=args.device_id, tags=args.tag)
        case "release":
            client.call("release", reservation_id=args.reservation_id)
            return {"reservation_id": args.reservation_id}
        case "reservations":
            return client.call("reservations")
        case "power":
            return client.call("power", device_id=args.device_id, action=args.action)
        case "serial":
            return _dispatch_serial(client, args)
        case "api-keys":
            return _dispatch_api_keys(client, args)
        case "status":
            return client.call("status")
        case "doctor":
            return client.call("doctor")
        case _:
            raise RuntimeError(f"unsupported command: {args.command}")


def _dispatch_serial(client: SocketRPCClient, args: argparse.Namespace) -> Any:
    match args.serial_command:
        case "open":
            return client.call("serial.open", device_id=args.device_id, baud_rate=args.baud_rate)
        case "read":
            return client.call(
                "serial.read",
                session_id=args.session_id,
                max_bytes=args.max_bytes,
                timeout=args.timeout,
            )
        case "write":
            return client.call(
                "serial.write",
                session_id=args.session_id,
                data=args.data,
                encoding=args.encoding,
            )
        case "close":
            client.call("serial.close", session_id=args.session_id)
            return {"session_id": args.session_id}
        case "run":
            return client.call(
                "serial.run",
                device_id=args.device_id,
                data=args.data,
                encoding=args.encoding,
                baud_rate=args.baud_rate,
                append_newline=not args.no_newline,
                max_bytes=args.max_bytes,
            )
        case _:
            raise RuntimeError(f"unsupported serial command: {args.serial_command}")


def _dispatch_api_keys(client: SocketRPCClient, args: argparse.Namespace) -> Any:
    match args.api_key_command:
        case "list":
            return client.call("api_keys.list")
        case "create":
            return client.call("api_keys.create", key_id=args.id, label=args.label)
        case "revoke":
            client.call("api_keys.revoke", key_id=args.key_id)
            return {"key_id": args.key_id}
        case _:
            raise RuntimeError(f"unsupported api-keys command: {args.api_key_command}")


def _print_result(result: Any, args: argparse.Namespace) -> None:
    if result is None:
        return
    if args.json:
        print(json.dumps(result, sort_keys=True))
        return
    formatted = _format_result(result, args)
    if formatted:
        print(formatted)


def _format_result(result: Any, args: argparse.Namespace) -> str:
    match args.command:
        case "registry" | "devices":
            return _format_devices(result)
        case "discover":
            return _format_discovered(result)
        case "reservations":
            return _format_reservations(result)
        case "doctor":
            return _format_doctor(result, verbose=bool(args.verbose))
        case "status":
            return _format_key_values(result)
        case "register":
            return _format_registration("Registered device", result)
        case "update":
            return _format_registration("Updated device", result)
        case "delete":
            return f"Deleted device: {result.get('device_id', '')}"
        case "reserve":
            return _format_reservation_detail("Reserved device", result)
        case "release":
            return f"Released reservation: {result.get('reservation_id', '')}"
        case "power":
            return _format_power_result(result)
        case "serial":
            return _format_serial_result(result, args)
        case "api-keys":
            return _format_api_keys_result(result, args)
        case _:
            return json.dumps(result, indent=2, sort_keys=True)


def _format_devices(result: dict[str, Any]) -> str:
    devices = result.get("devices", [])
    discovered = result.get("discovered", [])
    lines: list[str] = []
    if devices:
        lines.extend(_table(
            ["ID", "STATE", "SERIAL", "POWER", "NAME", "TAGS", "RESERVATION"],
            [
                [
                    device.get("id", ""),
                    device.get("state", ""),
                    _runtime_field(device, "serial_port"),
                    _runtime_field(device, "power_target"),
                    device.get("name") or "",
                    ",".join(device.get("tags", [])),
                    _reservation_summary(device.get("reservation")),
                ]
                for device in devices
            ],
        ))
    else:
        lines.append("No registered devices.")

    if discovered:
        lines.append("")
        lines.append("Discovered unregistered devices:")
        lines.extend(_format_runtime_rows(discovered))
    return "\n".join(lines)


def _format_discovered(result: list[dict[str, Any]]) -> str:
    if not result:
        return "No unregistered devices discovered."
    return "\n".join(_format_runtime_rows(result))


def _format_runtime_rows(items: list[dict[str, Any]]) -> list[str]:
    return _table(
        ["ID", "SERIAL", "POWER", "USB SERIAL", "USB PATH"],
        [
            [
                item.get("id", ""),
                (item.get("runtime") or {}).get("serial_port") or "",
                (item.get("runtime") or {}).get("power_target") or "",
                (item.get("runtime") or {}).get("usb_serial") or "",
                (item.get("runtime") or {}).get("usb_path") or "",
            ]
            for item in items
        ],
    )


def _format_reservations(result: list[dict[str, Any]]) -> str:
    if not result:
        return "No active reservations."
    return "\n".join(_table(
        ["ID", "DEVICE", "ATTRIBUTION"],
        [
            [
                reservation.get("id", ""),
                reservation.get("device_id", ""),
                _attribution_summary(reservation.get("attribution")),
            ]
            for reservation in result
        ],
    ))


def _format_registration(title: str, result: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"{title}: {result.get('id', '')}",
            f"Name: {result.get('name') or ''}",
            f"Tags: {', '.join(result.get('tags', [])) or ''}",
        ]
    )


def _format_reservation_detail(title: str, result: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"{title}: {result.get('device_id', '')}",
            f"Reservation: {result.get('id', '')}",
            f"Attribution: {_attribution_summary(result.get('attribution'))}",
        ]
    )


def _format_power_result(result: dict[str, Any]) -> str:
    lines = [
        f"Power {result.get('action', '')}: {result.get('device_id', '')}",
        f"Target: {result.get('power_target') or ''}",
    ]
    args = result.get("args")
    if args:
        lines.append(f"Command: {' '.join(str(arg) for arg in args)}")
    return "\n".join(lines)


def _format_serial_result(result: Any, args: argparse.Namespace) -> str:
    match args.serial_command:
        case "open":
            return "\n".join(
                [
                    f"Opened serial session: {result.get('id', '')}",
                    f"Device: {result.get('device_id', '')}",
                    f"Baud: {result.get('baud_rate', '')}",
                    f"Attribution: {_attribution_summary(result.get('attribution'))}",
                ]
            )
        case "read":
            return _format_serial_payload("Serial read", result)
        case "write":
            return f"Wrote {result.get('bytes_written', 0)} bytes to serial session {args.session_id}."
        case "close":
            return f"Closed serial session: {result.get('session_id', '')}"
        case "run":
            return _format_serial_payload("Serial response", result)
        case _:
            return json.dumps(result, indent=2, sort_keys=True)


def _format_serial_payload(title: str, result: dict[str, Any]) -> str:
    encoding = result.get("encoding", "")
    data = result.get("data", "")
    return "\n".join([f"{title} ({encoding}):", str(data)])


def _format_api_keys_result(result: Any, args: argparse.Namespace) -> str:
    match args.api_key_command:
        case "list":
            if not result:
                return "No API keys."
            return "\n".join(_table(
                ["ID", "LABEL", "REVOKED"],
                [
                    [
                        key.get("id", ""),
                        key.get("label", ""),
                        "yes" if key.get("revoked") else "no",
                    ]
                    for key in result
                ],
            ))
        case "create":
            api_key = result.get("api_key", {})
            return "\n".join(
                [
                    f"Created API key: {api_key.get('id', '')}",
                    f"Label: {api_key.get('label', '')}",
                    f"Secret: {result.get('secret', '')}",
                    "Store this secret now; it will not be shown again.",
                ]
            )
        case "revoke":
            return f"Revoked API key: {result.get('key_id', '')}"
        case _:
            return json.dumps(result, indent=2, sort_keys=True)


def _format_doctor(result: dict[str, Any], verbose: bool) -> str:
    checks = result.get("checks", [])
    lines = [f"Doctor: {'healthy' if result.get('ok') else 'unhealthy'}"]
    for check in checks:
        status = "ok" if check.get("ok") else "fail"
        lines.append(f"{status:4} {check.get('name', '')}: {check.get('detail', '')}")
        if verbose and not check.get("ok"):
            hint = _doctor_hint(str(check.get("name", "")), str(check.get("detail", "")))
            if hint:
                lines.append(f"     hint: {hint}")
    return "\n".join(lines)


def _doctor_hint(name: str, detail: str) -> str | None:
    if name == "uhubctl.installed":
        return "Install uhubctl and restart sbdevd so the daemon can find it on PATH."
    if name == "uhubctl.usable":
        if "Error initializing USB" in detail:
            return "Configure udev/group permissions for the USB hub, then reload udev or reconnect the hub."
        return "Run uhubctl as the daemon user and fix the reported failure before using power control."
    if name == "registry":
        return "Create the registry parent directory or adjust ownership so sbdevd can read and write it."
    if name == "socket_path":
        return "Create the socket parent directory or choose a writable path with SBDEVD_SOCKET."
    return None


def _format_key_values(result: dict[str, Any]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in sorted(result.items()))


def _runtime_field(device: dict[str, Any], field: str) -> str:
    runtime = device.get("runtime") or {}
    return runtime.get(field) or ""


def _reservation_summary(reservation: dict[str, Any] | None) -> str:
    if not reservation:
        return ""
    return f"{reservation.get('id', '')} {_attribution_summary(reservation.get('attribution'))}".strip()


def _attribution_summary(attribution: dict[str, Any] | None) -> str:
    if not attribution:
        return ""
    return f"{attribution.get('kind', '')}:{attribution.get('id', '')}"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    string_rows = [[str(cell) for cell in row] for row in rows]
    widths = [
        max(len(header), *(len(row[index]) for row in string_rows))
        for index, header in enumerate(headers)
    ]
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        "  ".join(row[index].ljust(widths[index]) for index in range(len(headers)))
        for row in string_rows
    )
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
