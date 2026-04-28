# Sysbench Device Manager

Manage UART-attached sysbench devices through a local daemon, a control CLI, an
HTTP/WebSocket SDK, and an MCP server. Use USB hub power control, serial
sessions, device registration, and attributed reservations from one host process.

Primarily designed for CS 140E/240LX/340LX use & the SysBench benchmark.

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

Use SDK/MCP clients:

```sh
SBDEVD_API_KEY=$SBDEVD_API_KEY uv run sbdevmcp --base-url http://127.0.0.1:8765
```

## Deployment

Run `sbdevd` on the machine with the USB hub and UART adapters. Let it own
device discovery, reservations, power operations, and serial sessions.

Use these client paths:

- Use `sbdevctl` for all management through the Unix socket.
- Use HTTP for automation: devices, reservations, power, and serial session
  open/close.
- Use WebSocket binary streams attached to serial sessions for bidirectional
  byte-oriented workflows such as bootloading.
- Use the async Python SDK and MCP server over the daemon HTTP/WebSocket API.
- Use API keys for HTTP/WebSocket reservation attribution.
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

Create the service user before installing the systemd unit. Replace `dialout`
with the group used in your udev rule if needed.

```sh
sudo useradd --system \
  --home-dir /var/lib/sysbench-devices \
  --create-home \
  --shell /usr/sbin/nologin \
  --groups dialout \
  sbdev
sudo install -d -o sbdev -g dialout -m 0755 /var/lib/sysbench-devices
```

If the user already exists, add it to the hardware-access group:

```sh
sudo usermod -a -G dialout sbdev
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
sudo udevadm control --reload-rules
sudo udevadm trigger --attr-match=subsystem=usb
```

Install and start the daemon service:

```sh
./setup.sh
```

`setup.sh` creates or updates the `sbdev` service user, removes old global
`/usr/local/bin/sbdev*` wrapper symlinks, stages source under
`/var/lib/sysbench-devices/src`, installs the package as `sbdev`, writes the
systemd unit, and restarts `sbdevd`.

Override defaults with environment variables when needed:

```sh
SBDEVD_GROUP=dialout SBDEVD_HTTP_PORT=8765 ./setup.sh
```

Install operator CLI/MCP tools separately:

```sh
uv tool install --force --reinstall --refresh --python 3.14 /path/to/sysbench-devices
sbdevctl --help
sbdevmcp --help
```

The generated system service uses:

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
ExecStart=/var/lib/sysbench-devices/.local/bin/sbdevd
Restart=on-failure

[Install]
WantedBy=multi-user.target
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
sbdevctl serial stream DEVICE_ID
sbdevctl serial close DEVICE_ID
```

`serial stream` connects stdin and stdout directly to the open device serial
stream.

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

Use MCP tools for daemon-backed operations:

- `list_devices`
- `list_reservations`
- `reserve`
- `release`
- `power`
- `open_serial`
- `read_serial`
- `write_serial`
- `close_serial`
- `bootload_file`

`open_serial` opens a daemon serial session and an MCP-owned async WebSocket
stream for the device ID. Use `read_serial`, `write_serial`, `close_serial`,
and `bootload_file` with that same device ID.

## Troubleshooting

Run doctor through the daemon:

```sh
sbdevctl doctor --verbose
```

Check these results:

- `uhubctl.installed`: install `uhubctl` if it is missing.
- `uhubctl.usable`: fix udev/group permissions or run `sbdevd` with sudo.
- `registry`: fix the registry path or file permissions.
- `socket_path`: fix the socket directory path or ownership.

Use `doctor --verbose` to debug discovery joins. It prints devices reported by
pyserial, devices parsed from `uhubctl`, and devices found in the registry.

If `sbdevctl` reports a missing socket, point it at the system daemon:

```sh
export SBDEVD_SOCKET=/run/sysbench-devices/sbdevd.sock
sbdevctl devices
```

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
