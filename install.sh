#!/usr/bin/env sh
set -eu

REPO_URL="git+https://github.com/orospor/siegerpc.git"
PYTHON_BIN="${PYTHON:-}"
INSTALL_MODE="auto"
VENV_DIR=""

usage() {
  cat <<'EOF'
Usage: install.sh [options]

Install siegerpc from GitHub.

Options:
  --user              Install into the current user's Python site packages
  --system            Install globally into the active Python environment
  --venv DIR          Create/use a virtual environment at DIR
  -h, --help          Show this help

Environment:
  PYTHON=/path/to/python3  Choose the Python interpreter

Examples:
  curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/install.sh | bash
  curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/install.sh | sudo bash -s -- --system
  curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/install.sh | bash -s -- --user
  curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/install.sh | bash -s -- --venv "$HOME/.local/share/siegerpc"
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --user)
      INSTALL_MODE="user"
      ;;
    --system)
      INSTALL_MODE="system"
      ;;
    --venv)
      INSTALL_MODE="venv"
      shift
      if [ "$#" -eq 0 ]; then
        echo "error: --venv requires a directory" >&2
        exit 2
      fi
      VENV_DIR="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

find_python() {
  if [ -n "$PYTHON_BIN" ]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
      echo "error: PYTHON points to an executable that was not found: $PYTHON_BIN" >&2
      exit 1
    }
    printf '%s\n' "$PYTHON_BIN"
    return
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    echo "error: python3 is required but was not found" >&2
    exit 1
  fi
}

PYTHON_BIN="$(find_python)"

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("error: siegerpc requires Python 3.10 or newer")
PY

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is required to install from GitHub" >&2
  exit 1
fi

if [ "$INSTALL_MODE" = "venv" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  PYTHON_BIN="$VENV_DIR/bin/python"
  PIP_ARGS=""
else
  PIP_ARGS=""
  if [ "$INSTALL_MODE" = "user" ]; then
    PIP_ARGS="--user"
  elif [ "$INSTALL_MODE" = "system" ]; then
    if "$PYTHON_BIN" -m pip install --help 2>/dev/null | grep -q -- "--break-system-packages"; then
      PIP_ARGS="--break-system-packages"
    fi
  fi
fi

"$PYTHON_BIN" -m ensurepip --upgrade >/dev/null 2>&1 || true
if [ "$INSTALL_MODE" = "venv" ]; then
  "$PYTHON_BIN" -m pip install --upgrade pip >/dev/null
fi

if [ -n "$PIP_ARGS" ]; then
  "$PYTHON_BIN" -m pip install --force-reinstall $PIP_ARGS "$REPO_URL"
else
  "$PYTHON_BIN" -m pip install --force-reinstall "$REPO_URL"
fi

BIN_DIR="$("$PYTHON_BIN" - <<'PY'
import sysconfig
print(sysconfig.get_path("scripts"))
PY
)"

if [ -x "$BIN_DIR/siegerpc" ]; then
  SIEGERPC_BIN="$BIN_DIR/siegerpc"
elif command -v siegerpc >/dev/null 2>&1; then
  SIEGERPC_BIN="$(command -v siegerpc)"
else
  echo "warning: siegerpc installed, but its script directory is not on PATH: $BIN_DIR" >&2
  echo "Run it with: $BIN_DIR/siegerpc" >&2
  exit 0
fi

"$SIEGERPC_BIN" --help >/dev/null

echo "siegerpc installed successfully:"
echo "  $SIEGERPC_BIN"
echo
echo "Example:"
echo "  siegerpc --url https://yourdomain.com/xmlrpc.php --duration 60 --concurrency 25 --rate 100 --i-own-this-server"
