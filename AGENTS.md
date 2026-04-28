# Repository Guidelines

## Project Structure & Module Organization

Source code lives in `sysbench_devices/`. Core daemon behavior is split across
`daemon.py`, `state.py`, `rpc.py`, and `http.py`. Hardware boundaries live in
`discovery.py`, `power.py`, `serial.py`, and `doctor.py`. Client entrypoints are
`client.py`, `ctl.py`, and `mcp.py`. Protocol-specific helpers live under
`sysbench_devices/protocols/`, including the CS140E bootloader.

Tests live in `tests/`. Hardware tests are isolated in `tests/integration/`.
Static parser fixtures live in `tests/fixtures/`. Project planning and user
documentation live in `PLAN.md` and `README.md`.

## Build, Test, and Development Commands

Use `uv` for local execution:

```sh
uv run pytest
uv run sbdevd --registry /tmp/sbdev/registry.toml --socket /tmp/sbdev/sbdevd.sock
uv run sbdevctl --socket /tmp/sbdev/sbdevd.sock doctor --verbose
uv run sbdevmcp --base-url http://127.0.0.1:8765
```

`uv run pytest` runs the default unit suite. Integration tests skip unless
explicitly enabled. Use destructive hardware tests only when attached devices
may be power-cycled:

```sh
SBDEV_HARDWARE=1 SBDEV_HARDWARE_DESTRUCTIVE=1 uv run pytest tests/integration/test_hardware.py
```

## Coding Style & Naming Conventions

Write Python 3.14 with type annotations for public functions and data flow.
Use 4-space indentation, `snake_case` for functions and variables, and
`PascalCase` for classes. Prefer dataclasses and small injected backends over
global state. Keep daemon, SDK, CLI, and MCP boundaries separate: the SDK and
MCP use HTTP only; `sbdevctl` is the only official Unix socket client.

No formatter or linter is configured yet. Match the surrounding style and keep
comments short and useful.

## Testing Guidelines

Use `pytest`. Name test files `test_*.py` and test functions `test_*`. Prefer
unit tests with fake discovery, power, serial, or SDK clients. Keep hardware
coverage opt-in through `SBDEV_HARDWARE=1`; require
`SBDEV_HARDWARE_DESTRUCTIVE=1` for off/cycle power tests.

## Commit & Pull Request Guidelines

This branch has no established commit history. Use concise imperative commit
messages, for example `Add MCP bootloader tool` or `Document uhubctl setup`.

Pull requests should describe behavior changes, list verification commands, and
call out hardware impact. Include relevant issue links when available. Do not
commit generated caches, local registry files, API key secrets, or machine-local
socket paths.

## Security & Configuration Tips

Treat API key secrets as write-only operator output. Store only hashed API keys
in the registry. Use udev or a dedicated service user for `uhubctl` access
instead of broad root execution when deploying long-running daemons.
