#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./setup.sh

Install and restart the sbdevd system service. Run from the repository root as
an operator with passwordless sudo, or as root with UV=/path/to/uv if uv is not
on root's PATH.

Environment overrides:
  SBDEVD_USER          service user, default: sbdev
  SBDEVD_GROUP         hardware access group, default: dialout
  SBDEVD_HOME          service home, default: /var/lib/sysbench-devices
  SBDEVD_SOURCE_DIR    source tree to stage, default: directory containing setup.sh
  SBDEVD_PYTHON        Python version for uv tool install, default: 3.14
  SBDEVD_SOCKET        daemon socket path, default: /run/sysbench-devices/sbdevd.sock
  SBDEVD_HTTP_HOST     HTTP bind host, default: 127.0.0.1
  SBDEVD_HTTP_PORT     HTTP bind port, default: 8765
  SBDEVD_LOG_LEVEL     daemon log level, default: INFO
  SBDEVD_CLEAN_OLD     remove old /usr/local symlinks and /opt install, default: 1
EOF
  exit 0
fi

if [[ $# -gt 0 ]]; then
  printf 'setup: error: unknown argument: %s\n' "$1" >&2
  printf 'Run ./setup.sh --help for usage.\n' >&2
  exit 2
fi

SERVICE_USER="${SBDEVD_USER:-sbdev}"
SERVICE_GROUP="${SBDEVD_GROUP:-dialout}"
INSTALL_HOME="${SBDEVD_HOME:-/var/lib/sysbench-devices}"
SOURCE_DIR="${SBDEVD_SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PYTHON_VERSION="${SBDEVD_PYTHON:-3.14}"
REGISTRY_PATH="${SBDEVD_REGISTRY:-${INSTALL_HOME}/registry.toml}"
SOCKET_PATH="${SBDEVD_SOCKET:-/run/sysbench-devices/sbdevd.sock}"
HTTP_HOST="${SBDEVD_HTTP_HOST:-127.0.0.1}"
HTTP_PORT="${SBDEVD_HTTP_PORT:-8765}"
LOG_LEVEL="${SBDEVD_LOG_LEVEL:-INFO}"
UNIT_PATH="${SBDEVD_UNIT_PATH:-/etc/systemd/system/sbdevd.service}"
CLEAN_OLD="${SBDEVD_CLEAN_OLD:-1}"

SOURCE_STAGE="${INSTALL_HOME}/src"
TOOL_BIN_DIR="${INSTALL_HOME}/.local/bin"
TOOL_DIR="${INSTALL_HOME}/.local/share/uv/tools"
UV_COPY="${TOOL_BIN_DIR}/uv"

log() {
  printf 'setup: %s\n' "$*"
}

die() {
  printf 'setup: error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

if [[ ${EUID} -eq 0 ]]; then
  run_root() {
    "$@"
  }

  run_service() {
    if command -v sudo >/dev/null 2>&1; then
      sudo -u "$SERVICE_USER" -H "$@"
    else
      runuser -u "$SERVICE_USER" -- "$@"
    fi
  }
else
  require_command sudo

  run_root() {
    sudo -n "$@"
  }

  run_service() {
    sudo -n -u "$SERVICE_USER" -H "$@"
  }
fi

UV_PATH="${UV:-}"
if [[ -z "$UV_PATH" ]]; then
  UV_PATH="$(command -v uv || true)"
fi
[[ -n "$UV_PATH" ]] || die "uv not found; install uv or run with UV=/path/to/uv"
[[ -x "$UV_PATH" ]] || die "uv is not executable: $UV_PATH"
[[ -f "${SOURCE_DIR}/pyproject.toml" ]] || die "source directory does not look like this repo: $SOURCE_DIR"

require_command rsync
require_command systemctl

log "source: ${SOURCE_DIR}"
log "service user: ${SERVICE_USER}"
log "install home: ${INSTALL_HOME}"

if [[ "$CLEAN_OLD" == "1" ]]; then
  log "removing old global wrapper symlinks, if present"
  for path in /usr/local/bin/sbdevd /usr/local/bin/sbdevctl /usr/local/bin/sbdevmcp; do
    if [[ -L "$path" ]]; then
      run_root rm -f "$path"
    elif [[ -e "$path" ]]; then
      log "leaving non-symlink in place: $path"
    fi
  done
  run_root rm -rf /opt/sysbench-devices
fi

if ! getent group "$SERVICE_GROUP" >/dev/null; then
  log "creating group ${SERVICE_GROUP}"
  run_root groupadd --system "$SERVICE_GROUP"
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  log "creating service user ${SERVICE_USER}"
  run_root useradd --system \
    --home-dir "$INSTALL_HOME" \
    --create-home \
    --shell /usr/sbin/nologin \
    --groups "$SERVICE_GROUP" \
    "$SERVICE_USER"
else
  log "updating service user group membership"
  run_root usermod -a -G "$SERVICE_GROUP" "$SERVICE_USER"
fi

log "creating install directories"
run_root install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0755 \
  "$INSTALL_HOME" \
  "$SOURCE_STAGE" \
  "$TOOL_BIN_DIR" \
  "${INSTALL_HOME}/.local/share/uv"

log "copying uv into service user home"
run_root install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0755 "$UV_PATH" "$UV_COPY"

log "staging source"
run_root rsync -a --delete \
  --exclude .git \
  --exclude .venv \
  --exclude init \
  --exclude build \
  --exclude dist \
  --exclude '*.egg-info' \
  --exclude __pycache__ \
  --exclude .pytest_cache \
  "${SOURCE_DIR}/" "${SOURCE_STAGE}/"
run_root chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$SOURCE_STAGE"

log "installing sbdevd tool as ${SERVICE_USER}"
run_service env \
  UV_TOOL_DIR="$TOOL_DIR" \
  UV_TOOL_BIN_DIR="$TOOL_BIN_DIR" \
  "$UV_COPY" tool install \
    --force \
    --reinstall \
    --refresh \
    --python "$PYTHON_VERSION" \
    "$SOURCE_STAGE"

log "writing systemd unit: ${UNIT_PATH}"
run_root tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=Sysbench Device Manager
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
RuntimeDirectory=sysbench-devices
StateDirectory=sysbench-devices
Environment=SBDEVD_REGISTRY=${REGISTRY_PATH}
Environment=SBDEVD_SOCKET=${SOCKET_PATH}
Environment=SBDEVD_HTTP_HOST=${HTTP_HOST}
Environment=SBDEVD_HTTP_PORT=${HTTP_PORT}
Environment=SBDEVD_LOG_LEVEL=${LOG_LEVEL}
ExecStart=${TOOL_BIN_DIR}/sbdevd
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

log "starting sbdevd"
run_root systemctl daemon-reload
run_root systemctl reset-failed sbdevd >/dev/null 2>&1 || true
run_root systemctl enable sbdevd
run_root systemctl restart sbdevd

log "verifying service"
run_root systemctl --no-pager --lines=12 status sbdevd

log "done"
printf '\nUse the socket with:\n'
printf '  export SBDEVD_SOCKET=%s\n' "$SOCKET_PATH"
printf '  sbdevctl doctor --verbose\n'
