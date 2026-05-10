#!/bin/sh
# Thin wrapper around install.py for Linux/macOS.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/install.py" "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python "$SCRIPT_DIR/install.py" "$@"
fi

echo "ERROR: Python not found. Run install.py with Python 3." 1>&2
exit 1
