# Sysbench Device Manager Plan

`sysbench-devices` is a Python rewrite and functional overhaul of
`../remote-pi-old`. The new system manages generic UART-attached devices, not
Raspberry Pi-specific hardware. Devices may be Pis, FPGAs, microcontrollers, or
anything else reachable through a UART and optionally controlled through a USB
hub port.

## Goals

- Python implementation, not Rust.
- Functional successor to `remote-pi`, not API-compatible with it.
- Local daemon named `sbdevd` with no web UI.
- Control tool named `sbdevctl`.
- MCP tool named `sbdevmcp`.
- All entrypoints installable as `uv` tools.
- Python SDK over the daemon HTTP/WebSocket API.
- HTTP API for operational use: device listing, reservations, power control,
  serial sessions, and serial streams.
- WebSocket API for bidirectional byte-oriented serial streams.
- Unix socket transport as an `sbdevctl`-only superset control surface: all
  management operations plus local operational capabilities.
- API keys for HTTP/WebSocket reservation attribution; Unix socket reservations
  use a special `admin` attribution ID.
- No users, passwords, login sessions, role model, or web UI.
- Explicit device registration.
- TOML registry storage.
- Tags are plain strings, not key/value pairs.
- Use short-ish lowercase hex IDs, not long UUIDs.
- Real unit tests around discovery, state, socket RPC, HTTP API, CLI, SDK/MCP
  service logic, serial sessions, power control, API key/admin attribution, and
  diagnostics.
- Runtime debugging through `sbdevctl doctor`, backed by a daemon doctor RPC.

## Non-Goals

- No browser UI or WebSocket UI.
- No full user model or permission model. API keys identify HTTP/WebSocket
  reservation owners, and Unix socket reservations use the special `admin`
  attribution ID; neither is a general account system.
- No direct public Unix socket clients other than `sbdevctl`.
- No compatibility with the old HTTP endpoint shapes, old CLI arguments, or old
  SDK surface.
- No Raspberry Pi naming in core models or commands.
- No automatic registration of newly discovered devices.

## Functionality From `remote-pi`

Carry forward:

- Device discovery from `uhubctl` plus serial-port metadata.
- Stable short hex device identity derived from physical USB location and USB
  serial where available.
- Explicit registry of managed devices with names and tags.
- Reconciliation of registered devices against live discovery.
- Separate reporting of unregistered but discovered devices.
- Reservations to prevent conflicting concurrent access, attributed to API keys
  for HTTP callers and to `admin` for Unix socket callers.
- Power control using `uhubctl` actions: `on`, `off`, `cycle`.
- UART open/stream/close sessions.
- UART baud-rate configuration.
- Native binary frames for serial streams.
- CS140E/RPi bootloader protocol helper as an optional protocol module.

Delete or replace:

- Rust workspace.
- SQLite database.
- Web UI.
- Users, passwords, browser sessions, and bearer-token login flows.
- Admin/setup web behavior.
- Pi-specific naming in public concepts.

## Package Layout

```text
sysbench_devices/
  __init__.py
  models.py
  errors.py

  daemon.py
  rpc.py
  http.py
  state.py
  registry.py
  api_keys.py

  discovery.py
  power.py
  serial.py
  doctor.py

  client.py
  ctl.py
  mcp.py

  protocols/
    __init__.py
    cs140e_bootloader.py

tests/
  unit/
  integration/
```

## Module Responsibilities

`models.py`

- Dataclasses and enums for `Device`, `DiscoveredDevice`, `Reservation`,
  `ReservationAttribution`, `RuntimeDevice`, `UartConfig`, `PowerAction`,
  `ApiKeyRecord`, `DoctorReport`, HTTP payloads, and management RPC payloads.
- Use generic public naming: `device`, `runtime`, `serial`, `power_target`.
- Use short lowercase hex strings for public IDs. Avoid UUID-length IDs for
  devices, reservations, and serial sessions.
- Never expose stored API key secrets. Reservations should expose attribution
  such as key ID and label, or the built-in `admin` socket attribution, not the
  raw key.

Tests:

- Serialization shape.
- State values.
- Tag matching behavior.
- API key metadata, socket `admin` attribution, and reservation attribution
  shape.
- Stable sorting helpers if implemented.

`registry.py`

- Load and save the TOML registry, including managed devices and API key
  records.
- Write atomically.
- Validate duplicate IDs, duplicate tags, empty tags, malformed records, and API
  key ID/hash shape.

Example registry:

```toml
[[devices]]
id = "a4c91f2b"
name = "nexys-a7-01"
tags = ["fpga", "uart", "nexys-a7"]

[[devices]]
id = "6e02bd90"
name = "rpi-zero-01"
tags = ["pi", "uart", "cs140e"]

[[api_keys]]
id = "autograder"
label = "CS140E autograder"
key_hash = "..."
```

Tests:

- Empty registry.
- Round-trip read/write.
- Atomic save behavior.
- Duplicate validation.
- String tag parsing.
- API key record validation.

`api_keys.py`

- Generate, hash, and verify HTTP API keys.
- Return the raw API key only at creation time.
- Resolve a presented HTTP API key to stable attribution metadata for
  reservations.
- Leave the built-in `admin` socket attribution out of API key storage; it is a
  fixed local-daemon identity, not a generated credential.
- Keep key labels operator-facing and non-secret.

Tests:

- Key generation shape.
- Hash verification.
- Unknown/revoked key behavior.
- Attribution metadata returned without raw secrets.
- Fixed `admin` socket attribution is not treated as an API key record.

`discovery.py`

- Parse `uhubctl` output.
- Enumerate serial ports through `pyserial`.
- Join hub-port data and serial-port metadata into `RuntimeDevice` records.
- Keep the join logic pure and directly testable.

Tests:

- Single hub and multiple hub parsing.
- Disconnected ports.
- Missing `uhubctl`.
- Serial ports with no power-controllable port.
- Duplicate VID/PID handling.
- USB serial disambiguation.
- Stable device ID generation.

ID guidance:

- Device IDs should be deterministic short hex strings, likely 8 to 12 hex
  characters.
- Derive them from stable discovery identity such as USB path plus USB serial
  when present.
- Keep the full discovery identity in runtime metadata for debugging; expose the
  short hex ID as the operator-facing handle.
- If a hash collision occurs during reconciliation or registration, fail loudly
  and include both underlying discovery identities in the error.

`state.py`

- Own daemon state.
- Reconcile registry, live discovery, and reservations.
- Register, update, delete, list, discover, reserve, release, power, and serial
  session orchestration.
- Keep reservations in memory only.
- Attach API key attribution to reservations created through the HTTP API and
  `admin` attribution to reservations created through the Unix socket.
- Generate short hex reservation IDs where an ID is needed.

Tests:

- Register requires live discovered device.
- Registered device states: `online`, `offline`, `reserved`.
- Discovered devices exclude registered devices.
- Reserve by ID.
- Reserve by all requested tags.
- Reservation conflict behavior.
- Reservation attribution from API keys and socket `admin`.
- Explicit release.
- Offline reserved devices remain reserved until release or daemon restart.

`rpc.py`

- Unix socket protocol for the `sbdevctl` superset control surface.
- Prefer newline-delimited JSON for request/response methods and raw bytes for
  serial stream mode.
- Map daemon exceptions into structured error responses.
- Do not treat this protocol as a public SDK surface. The only official client
  is `sbdevctl`.
- Management methods include registry mutation, registration discovery, API key
  create/list/revoke, daemon status, and doctor diagnostics.
- Operational methods include the same listing, reservation, power, and serial
  capabilities exposed over HTTP.
- Reservations created through the socket always use the special `admin`
  attribution ID.

Tests:

- Client/server round trips.
- Malformed JSON.
- Unknown method.
- Error propagation.
- Socket cleanup.
- Superset method coverage for management and operational methods.
- Socket reservations use `admin` attribution.

`http.py`

- HTTP API for the operational surface exposed to automation and tools.
- Provide endpoints for device listing, reservation create/release/list, power
  actions, and serial session open/close.
- Provide a WebSocket endpoint for bidirectional serial byte streams attached to
  open serial sessions.
- Require an API key for reservation creation so reservations can be attributed
  to a stable caller. Use the same attribution for reservation-scoped power and
  serial operations.
- Do not expose registry mutation, API key management, daemon doctor, or other
  management operations over HTTP.
- Use binary WebSocket frames for serial payloads.
- Keep HTTP operational semantics aligned with the matching Unix socket
  operational methods.

Expected endpoint groups:

```text
GET    /devices
GET    /reservations
POST   /reservations
DELETE /reservations/{reservation-id}
POST   /devices/{device-id}/power
POST   /devices/{device-id}/serial
DELETE /devices/{device-id}/serial
WS     /devices/{device-id}/serial/stream
```

Tests:

- API key attribution on reservation creation.
- HTTP reservations never use the socket `admin` attribution.
- Unknown or revoked API key rejection.
- Device and reservation listing shapes.
- Power action dispatch.
- Serial session lifecycle.
- Binary payload helpers.
- Management operations are not reachable over HTTP.

`daemon.py`

- `sbdevd` entrypoint.
- Load config and registry.
- Bind the Unix socket superset control surface and the HTTP API listener.
- Dispatch socket RPC and HTTP calls into `state.py`.
- Handle shutdown cleanly.

Tests:

- Startup with temp registry, temp socket, and temp HTTP listener.
- Socket RPC and HTTP smoke tests against an in-process daemon.
- Socket path handling.
- HTTP bind configuration.

`client.py`

- Async Python SDK over the daemon HTTP/WebSocket API.
- Thin `anyio`-cancellable methods matching the public operational surface.
- No Unix socket transport and no management RPC calls.
- Accept an API key for reservation attribution and reservation-scoped
  operations.

Tests:

- Fake HTTP transport.
- Error mapping.
- Binary payload helpers.

`ctl.py`

- `sbdevctl` CLI.
- Human-facing commands over the Unix socket superset control surface.
- The official and only supported client for the Unix socket.
- Route management operations and local operational convenience commands through
  the socket. Socket reservations are attributed to `admin`.

Expected command groups:

```text
sbdevctl registry
sbdevctl discover
sbdevctl register <device-id> [--name NAME] [--tag TAG ...]
sbdevctl update <device-id> [--name NAME] [--tag TAG ...]
sbdevctl delete <device-id>
sbdevctl devices
sbdevctl reserve <device-id>
sbdevctl reserve --tag TAG [--tag TAG ...]
sbdevctl release <reservation-id>
sbdevctl reservations
sbdevctl power <device-id> on|off|cycle
sbdevctl serial open/stream/close
sbdevctl api-keys list
sbdevctl api-keys create --id ID --label LABEL
sbdevctl api-keys revoke <key-id>
sbdevctl status
sbdevctl doctor
```

Tests:

- CLI argument parsing.
- Output formatting.
- Exit codes.
- Fake socket RPC behavior.
- Socket reservation commands use `admin` attribution.

`serial.py`

- Open serial devices with configurable baud rate.
- Provide daemon-owned serial sessions.
- Support read-until-quiet behavior for one-shot commands.

Tests:

- Fake serial stream read/write.
- Timeouts.
- Baud-rate configuration.
- Session close cleanup.

`power.py`

- Build and run `uhubctl` commands.
- Summarize permission and command failures.
- Keep command runner injectable.

Tests:

- Args for `on`, `off`, `cycle`.
- Cycle delay.
- Missing command.
- Permission-denied output.
- Nonzero exit output summary.

`doctor.py`

- Daemon-side runtime diagnostics.
- `sbdevctl doctor` invokes the daemon RPC and reports host state.
- Check `uhubctl` availability and whether the daemon process can run `uhubctl`
  successfully with its current permissions.
- Keep permission diagnostics at the same abstraction boundary as the daemon:
  the daemon interacts with `uhubctl`, not raw `/dev/bus/usb` or sysfs power
  files, so doctor should report `uhubctl` installed/missing and
  usable/not-usable.
- Also report registry readability/writability, socket path status, and HTTP
  bind status.
- Doctor is a management-only operation and is not exposed through the HTTP API,
  SDK, or MCP server.

Tests:

- Fake command runner reports missing `uhubctl`.
- Fake command runner reports non-sudo `uhubctl` permission failure.
- No supported hubs.
- Registry path failure.
- Healthy report.

`mcp.py`

- `sbdevmcp` stdio MCP server.
- Built on the Python SDK and therefore limited to the daemon operational API.
- No direct hardware logic.
- No Unix socket or management RPC access.
- Keep serial session lifecycle management in the MCP service layer.

Tests:

- Fake SDK service tests.
- Open/write/read/close session lifecycle.
- Reserve/release cleanup on errors.
- Binary encoding helpers.

`protocols/cs140e_bootloader.py`

- Protocol-specific bootloader helper carried forward from the old Python SDK
  and exposed through SDK/MCP helpers that use WebSocket serial streams.
- Do not add daemon-native bootloader state or a dedicated bootloader HTTP
  endpoint unless the operational HTTP surface is intentionally expanded later.
- Publicly named as CS140E/RPi-specific, not a core daemon concept.

Tests:

- Successful upload.
- Print-string handling.
- CRC mismatch.
- Boot error.
- Garbage before sync word.
- Extra `GET_PROG_INFO` handling.

## Reconciliation Model

Reconciliation combines three inputs:

```text
TOML registry + live hardware scan + in-memory attributed reservations => current device view
```

The registry is authoritative for which devices are managed. Discovery is
authoritative for what hardware is physically reachable right now. Reservations
are runtime-only state.

Algorithm:

1. Run discovery and build `live_by_id: dict[str, RuntimeDevice]`.
2. Load the TOML registry and build `registered_by_id`.
3. For every registered device:
   - If the ID is not live, report state `offline`.
   - If the ID is live and reserved, report state `reserved`.
   - If the ID is live and not reserved, report state `online`.
   - Attach runtime fields only when live.
4. For every live device not present in the registry, return it as
   `discovered`, not as a managed device.
5. Sort registered devices and discovered devices for stable output.

Reservations are not silently dropped during reconciliation. If a reserved
device goes offline, it remains reserved, with its API key or socket `admin`
attribution, until explicit release or daemon restart. This matches the existing
`remote-pi` behavior more closely and avoids hidden state changes during
read-oriented operations.

## When Reconciliation Runs

Keep the same broad behavior as `remote-pi`: reconciliation is demand-driven.

Run reconciliation for:

- HTTP device listing.
- HTTP reservation create/list.
- Socket device listing.
- Socket reservation create/list.
- Management `discover`.
- Management `register`.
- Explicit management `rescan`.

Do not require full reconciliation for every power or serial operation. Those
operations should resolve against known runtime state and perform targeted fresh
checks when needed.

The explicit management `rescan` command is just a forced reconciliation that
returns the new current view. It does not need separate background scanner
machinery.

## Transport Model

The daemon has two local control surfaces with different stability guarantees:

- The Unix socket is a superset control surface for management and operational
  convenience. It is an internal daemon protocol whose only official client is
  `sbdevctl`.
- The HTTP API is the supported automation surface for device listing,
  reservations, power control, and serial operations.
- The Python SDK and MCP server use only the HTTP API. They must not call the
  Unix socket directly or expose management operations.
- API keys are presented to the HTTP API to attribute reservations and
  reservation-scoped work to a stable caller.
- Reservations created through the Unix socket use the special `admin`
  attribution ID.

## Unix Socket Path

`XDG_RUNTIME_DIR` is not the same as `/var/run`.

- `/var/run` is usually a symlink to `/run`.
- `$XDG_RUNTIME_DIR` is per-user, usually `/run/user/$UID`.
- A system daemon should default to `/run/sysbench-devices/sbdevd.sock`.
- A user/dev daemon may use `$XDG_RUNTIME_DIR/sysbench-devices/sbdevd.sock`.
- Always allow override through `SBDEVD_SOCKET`.

## Implementation Status

Completed baseline:

- Scaffolded the `sysbench_devices` package, `README.md`, console scripts, and
  pytest configuration.
- Implemented shared models, TOML registry storage, API key generation/hash
  verification, and reserved `admin` socket attribution.
- Implemented daemon state with in-memory reservations, device reconciliation,
  fake-backed power and serial orchestration, and API-key/admin attribution.
- Implemented the Unix socket RPC as the `sbdevctl`-only superset control
  surface.
- Implemented the HTTP API for device listing, reservations, power, and serial
  operations.
- Implemented the Python SDK over HTTP only.
- Implemented `sbdevctl` commands over the Unix socket for management plus local
  operational convenience.
- Implemented a minimal SDK-backed `sbdevmcp` stdio facade.
- Added focused tests for discovery parsing, registry/API keys, HTTP
  attribution, socket `admin` attribution, and RPC/HTTP smoke behavior.
- Verified the baseline with `uv run pytest`.
- Phase 2 hardware discovery work completed so far:
  - Added `pyserial` as a runtime dependency.
  - Expanded `uhubctl` parsing for real VIA Labs USB2/USB3 hub output.
  - Parse connected-device VID/PID/product/serial from `uhubctl` port lines.
  - Join `uhubctl` ports to `pyserial` ports by USB serial, with USB location
    fallback.
  - Filter serial enumeration to USB-like serial devices so legacy `/dev/ttyS*`
    ports do not appear as discovered devices.
  - Treat hub-to-hub links as topology, not target devices.
  - Added fixture tests using the observed VIA hub topology.
  - Made `UhubctlPowerBackend` command construction injectable and tested.
  - Expanded doctor to check `uhubctl` installed/usable at the daemon command
    boundary.
  - Verified live read-only discovery returns the four CP2102N devices on
    `3-7.3` ports 1-4 with `/dev/ttyUSB*` serial paths and power targets.
  - Re-verified with `uv run pytest`.
- Phase 3 operator polish completed so far:
  - Added human-readable `sbdevctl` formatting for devices, discovered devices,
    reservations, status, and doctor output.
  - Added human-readable `sbdevctl` formatting for mutation commands,
    API-key commands, power commands, and serial session commands.
  - Added `sbdevctl doctor --verbose` remediation hints while keeping `--json`
    available for machine output.
  - Added opt-in hardware integration tests guarded by `SBDEV_HARDWARE=1`.
  - Hardware tests cover live discovery, safe `uhubctl -a on`, temp-registry
    register/reserve, and real serial open/close.
  - Added a destructive hardware test guarded by both `SBDEV_HARDWARE=1` and
    `SBDEV_HARDWARE_DESTRUCTIVE=1`. It powers off two devices, powers them back
    on, and verifies the same device IDs map to the same power targets.
  - Verified default tests with `uv run pytest`: hardware tests skip unless
    opted in.
  - Verified hardware tests with
    `SBDEV_HARDWARE=1 uv run pytest tests/integration/test_hardware.py`.
  - Verified the destructive two-device off/on test with
    `SBDEV_HARDWARE=1 SBDEV_HARDWARE_DESTRUCTIVE=1 uv run pytest
    tests/integration/test_hardware.py::test_live_destructive_two_devices_keep_ids_on_same_ports_after_off_on`.
- Phase 4 logging and MCP work completed so far:
  - Added `logging` package configuration through `sysbench_devices.logging_config`.
  - Added `--log-level` / environment-driven logging for `sbdevd`.
  - Added logging to daemon startup/shutdown, Unix socket RPC, and HTTP request
    handling.
  - Replaced the placeholder MCP JSON loop with a real FastMCP server from the
    official `modelcontextprotocol/python-sdk` package.
  - Kept MCP on the SDK side of the boundary; it still exposes only daemon
    operational capabilities and no Unix socket management operations.
  - Added `mcp>=1.0.0` as a runtime dependency.
  - Added tests for logging configuration and MCP service/server construction.
  - Verified with `uv run pytest`.
- Phase 5 CS140E bootloader work completed so far:
  - Ported the CS140E bootloader framing, CRC echo, `PRINT_STRING`,
    `BOOT_ERROR`, and `BOOT_SUCCESS` handling into async `anyio` code in
    `protocols/cs140e_bootloader.py`.
  - Added a WebSocket serial stream adapter that reads and writes binary frames
    for open serial sessions.
  - Added SDK stream-based helpers `bootload` and `bootload_file`.
  - Added MCP `bootload_file`, backed by the open MCP serial stream.
  - Added bootloader protocol tests for success, print-string handling, boot
    errors, CRC mismatch, garbage before sync, extra `GET_PROG_INFO`, and SDK
    session lifecycle.
  - Verified with `uv run pytest`.
- Phase 6 documentation work completed so far:
  - Replaced `README.md` with concise docs for the daemon, `sbdevctl`, HTTP
    clients, MCP, and troubleshooting.
  - Added a no-systemd `sudo` tl;dr path.
  - Added recommended udev and systemd examples for `sbdevd`.
  - Documented the socket-vs-HTTP split: `sbdevctl` is the only official socket
    client, while SDK/MCP use HTTP API operations with API keys.
  - Documented doctor mode as checking `uhubctl` installed/usable at the daemon
    command boundary.

Known incomplete areas:

- Hardware tests intentionally avoid `off`/`cycle` unless
  `SBDEV_HARDWARE_DESTRUCTIVE=1` is set.

## Phase 2 Plan

Phase 2 should make the hardware boundary real and diagnosable.

1. Done: add `pyserial` as a runtime dependency and enumerate serial ports through
   `serial.tools.list_ports`.
2. Done: expand `uhubctl` parsing using the observed VIA Labs topology:
   `2109:0817` USB3 hub and `2109:2817` USB2 hub, with CP2102N devices on
   `3-7.3` ports 1-4.
3. Done: parse connected-device VID, PID, product string, and USB serial from
   `uhubctl` port lines.
4. Done: join `uhubctl` port metadata to `pyserial` metadata using USB serial first,
   then USB path as a fallback.
5. Done: treat hub-to-hub ports as topology links, not controllable target
   devices. Never choose daisy-chain ports for registration/power operations.
6. Done: make stable device IDs derive from the joined physical identity and USB
   serial, with collision tests.
7. Mostly done: improve `UhubctlPowerBackend` for precise target selection.
   `uhubctl` itself handles the dual USB2/USB3 hub side when invoked with the
   USB2 target, as verified by non-sudo `uhubctl -l 3-7.3 -p 1 -a on`.
8. Done: expand `doctor` to run inside the daemon and report whether `uhubctl` is
   installed and usable with the daemon's current permissions, plus registry
   access, socket path access, and HTTP bind status.
9. Done: add a `sbdevctl doctor --verbose` view with concrete remediation hints,
   including a note to configure udev/group permissions when `uhubctl` is
   installed but not usable.
10. Done: add hardware-free fixture tests from the real `sudo uhubctl` output and fake
    `pyserial` list-port records.
11. Done: add opt-in hardware integration tests for listing, register/reserve,
    serial open/close, and safe power `on` checks before testing `off/cycle`.

## Phase 3 Plan

Phase 3 focuses on operator polish and opt-in hardware confidence.

1. Done: add human-readable `sbdevctl` output for common inspection commands:
   `devices`, `discover`, `reservations`, `status`, and `doctor`.
2. Done: keep `--json` as the stable machine-readable CLI output mode.
3. Done: add `sbdevctl doctor --verbose` remediation hints for missing
   `uhubctl`, unusable `uhubctl`, registry access, and socket path access.
4. Done: add default-skipped hardware integration tests gated by
   `SBDEV_HARDWARE=1`.
5. Done: cover live discovery, safe power `on`, temp-registry registration,
   admin reservation, and real serial open/close in hardware tests.
6. Done: add nicer human output for mutation commands such as `register`,
   `reserve`, `release`, `api-keys create`, and serial session commands.
7. Done: add explicit destructive hardware tests for `off`/`on`, but keep them
   behind a second opt-in environment variable so they cannot run by accident.
   The current destructive case turns off two devices, turns them back on, and
   verifies the same device IDs return on the same ports.

## Phase 4 Plan

Phase 4 implements real logging and real MCP.

1. Done: add standard-library `logging` configuration.
2. Done: add daemon `--log-level` and `SBDEVD_LOG_LEVEL`.
3. Done: log daemon lifecycle, socket RPC calls/failures, and HTTP
   calls/failures.
4. Done: replace the placeholder MCP stdio JSON loop with FastMCP from the
   official Python MCP SDK.
5. Done: expose SDK-backed MCP tools for daemon operational capabilities:
   devices, reservations, reserve/release, power, serial open/read/write/close,
   and bootload file upload.
6. Done: add MCP/logging unit tests.

## Phase 5 Plan

Phase 5 implements the CS140E bootloader in the SDK and MCP.

1. Done: port the CS140E bootloader framing, CRC, status handling, and error
   handling into async `anyio` code in `protocols/cs140e_bootloader.py`.
2. Done: add SDK methods that expose the bootloader over WebSocket serial
   streams.
3. Done: add an MCP bootloader upload tool backed by the SDK. This uses the
   WebSocket serial stream capability; it does not add a dedicated bootloader
   endpoint to the daemon.
4. Done: add fixture-heavy unit tests for success, boot errors, CRC mismatch,
   garbage before sync, print-string handling, extra `GET_PROG_INFO`, and SDK
   session lifecycle.

## Phase 6 Plan

Phase 6 writes complete user documentation.

1. Done: replace `README.md` with concise documentation using imperative, present
   tense language and minimal examples.
2. Done: include sections: `tl;dr`, deployment, `sbdevctl` configuration, MCP
   sample configuration, and troubleshooting through doctor mode.
3. Done: include recommended udev and systemd configuration while keeping a
   simple sudo/no-systemd path in `tl;dr`.

## Testing Expectations

Tests should be mostly unit tests with injected dependencies. Hardware tests
should be explicit integration tests, not required for the default test suite.

Default verification target:

```text
uv run pytest
```

The core rule: parsing, reconciliation, registry mutation, socket RPC, HTTP
operations, API key/admin attribution, and MCP session cleanup must all be
testable without physical hardware.
