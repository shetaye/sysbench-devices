# Sysbench Device Manager

Manage UART-attached sysbench devices through a local daemon, a control CLI, an
HTTP SDK, and an MCP server. Use USB hub power control, serial sessions, device
registration, and attributed reservations from one host process.

## tl;dr

Run a local daemon without systemd:

```sh
sudo --preserve-env=PATH uv run sbdevd \
  --registry /tmp/sysbench-devices/registry.toml \
  --socket /tmp/sysbench-devices/sbdevd.sock \
  --http-host 127.0.0.1 \
  --http-port 8765
```

Use the control CLI:

```sh
export SBDEVD_SOCKET=/tmp/sysbench-devices/sbdevd.sock
uv run sbdevctl doctor --verbose
uv run sbdevctl discover
uv run sbdevctl register DEVICE_ID --name board-01 --tag uart
uv run sbdevctl devices
```

Create an HTTP API key:

```sh
uv run sbdevctl api-keys create --id local --label "Local tools"
export SBDEVD_API_KEY=PASTE_SECRET_HERE
```

Use HTTP-backed clients:

```sh
SBDEVD_API_KEY=$SBDEVD_API_KEY uv run sbdevmcp --base-url http://127.0.0.1:8765
```

## Deployment

Run `sbdevd` on the machine with the USB hub and UART adapters. Let it own
device discovery, reservations, power operations, and serial sessions.

Use these client paths:

- Use `sbdevctl` for all management through the Unix socket.
- Use HTTP for automation: devices, reservations, power, serial sessions, and
  serial run.
- Use the Python SDK and MCP server over HTTP only.
- Use API keys for HTTP reservation attribution.
- Expect Unix socket reservations to use the built-in `socket:admin`
  attribution.

Set these daemon variables when useful:

```sh
SBDEVD_REGISTRY=/var/lib/sysbench-devices/registry.toml
SBDEVD_SOCKET=/run/sysbench-devices/sbdevd.sock
SBDEVD_HTTP_HOST=127.0.0.1
SBDEVD_HTTP_PORT=8765
SBDEVD_LOG_LEVEL=INFO
```

Configure udev so `uhubctl` works without sudo. Replace `2109` with the vendor
ID for each controllable hub.

```udev
# /etc/udev/rules.d/52-sysbench-devices-usb.rules
SUBSYSTEM=="usb", DRIVER=="hub|usb", ATTR{idVendor}=="2109", MODE="0664", GROUP="dialout"
SUBSYSTEM=="usb", DRIVER=="hub|usb", ATTR{idVendor}=="2109", RUN+="/bin/sh -c 'chown -f root:dialout $sys$devpath/*port*/disable || true'"
SUBSYSTEM=="usb", DRIVER=="hub|usb", ATTR{idVendor}=="2109", RUN+="/bin/sh -c 'chmod -f 660 $sys$devpath/*port*/disable || true'"
```

Apply the rule:

```sh
sudo usermod -a -G dialout sbdev
sudo udevadm control --reload-rules
sudo udevadm trigger --attr-match=subsystem=usb
```

Install a system service after `sbdevd` is on `PATH`:

```ini
# /etc/systemd/system/sbdevd.service
[Unit]
Description=Sysbench Device Manager
After=network.target

[Service]
Type=simple
User=sbdev
Group=dialout
RuntimeDirectory=sysbench-devices
StateDirectory=sysbench-devices
Environment=SBDEVD_REGISTRY=/var/lib/sysbench-devices/registry.toml
Environment=SBDEVD_SOCKET=/run/sysbench-devices/sbdevd.sock
Environment=SBDEVD_HTTP_HOST=127.0.0.1
Environment=SBDEVD_HTTP_PORT=8765
ExecStart=/usr/local/bin/sbdevd
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Start it:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now sbdevd
```

## Configuration via `sbdevctl`

Point the CLI at the daemon socket:

```sh
export SBDEVD_SOCKET=/run/sysbench-devices/sbdevd.sock
```

Inspect host state:

```sh
sbdevctl doctor --verbose
sbdevctl discover
sbdevctl devices
```

Register devices explicitly:

```sh
sbdevctl register DEVICE_ID --name nexys-a7-01 --tag fpga --tag uart
sbdevctl update DEVICE_ID --name nexys-a7-02 --tag fpga --tag uart
sbdevctl delete DEVICE_ID
```

Reserve and release devices:

```sh
sbdevctl reserve DEVICE_ID
sbdevctl reserve --tag fpga --tag uart
sbdevctl reservations
sbdevctl release RESERVATION_ID
```

Control power:

```sh
sbdevctl power DEVICE_ID on
sbdevctl power DEVICE_ID off
sbdevctl power DEVICE_ID cycle
```

Use serial:

```sh
sbdevctl serial open DEVICE_ID --baud-rate 115200
sbdevctl serial write SESSION_ID "help"
sbdevctl serial read SESSION_ID --max-bytes 4096 --timeout 0.2
sbdevctl serial close SESSION_ID
sbdevctl serial run DEVICE_ID "status"
```

Manage HTTP API keys:

```sh
sbdevctl api-keys list
sbdevctl api-keys create --id autograder --label "CS140E autograder"
sbdevctl api-keys revoke autograder
```

Add `--json` to any `sbdevctl` command for machine-readable output.

## MCP

Create an API key with `sbdevctl`, then run `sbdevmcp` against the daemon HTTP
API.

```json
{
  "mcpServers": {
    "sysbench-devices": {
      "command": "sbdevmcp",
      "args": ["--base-url", "http://127.0.0.1:8765"],
      "env": {
        "SBDEVD_API_KEY": "sbdev_..."
      }
    }
  }
}
```

Use MCP tools for HTTP-backed operations only:

- `list_devices`
- `list_reservations`
- `reserve`
- `release`
- `power`
- `serial_open`
- `serial_read`
- `serial_write`
- `serial_close`
- `serial_run`
- `bootload_binary`

Send `bootload_binary` payloads as `base64`, `hex`, or `utf-8`. The tool opens
an HTTP serial session, runs the CS140E bootloader protocol, and closes the
session.

## Troubleshooting

Run doctor through the daemon:

```sh
sbdevctl doctor --verbose
```

Check these results:

- `uhubctl.installed`: install `uhubctl` if it is missing.
- `uhubctl.usable`: fix udev/group permissions or run `sbdevd` with sudo.
- `registry`: fix the registry path or file permissions.
- `socket`: fix the socket directory path or ownership.
- `http`: fix the bind host or port.

Verify hub permissions at the same boundary the daemon uses:

```sh
uhubctl
uhubctl -l HUB_LOCATION -p PORT -a on
```

If those commands fail as the daemon user, fix udev or group membership. Log out
and back in after changing groups.

Use destructive power tests only when hardware can be power-cycled:

```sh
SBDEV_HARDWARE=1 SBDEV_HARDWARE_DESTRUCTIVE=1 uv run pytest tests/integration/test_hardware.py
```
