#!/bin/sh

set -eu

REPOSITORY="xoellijo/pnpink"
VERSION="latest"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version)
      [ "$#" -ge 2 ] || { echo "ERROR: --version requires a value" >&2; exit 2; }
      VERSION=$2
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [version]"
      echo "       $0 --version 0.55"
      exit 0
      ;;
    *)
      [ "$VERSION" = "latest" ] || { echo "ERROR: unexpected argument: $1" >&2; exit 2; }
      VERSION=$1
      shift
      ;;
  esac
done

VERSION=${VERSION#v}
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pnpink_bootstrap.XXXXXX")
trap 'rm -rf "$TEMP_DIR"' EXIT HUP INT TERM

download() {
  url=$1
  destination=$2
  echo "Downloading $url"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --connect-timeout 20 -o "$destination" "$url"
    return
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -O "$destination" "$url"
    return
  fi
  echo "ERROR: curl or wget is required." >&2
  exit 1
}

if [ "$VERSION" = "latest" ]; then
  BASE_URL="https://github.com/$REPOSITORY/releases/latest/download"
  PAYLOAD_NAME="pnpink_payload_latest.zip"
else
  BASE_URL="https://github.com/$REPOSITORY/releases/download/v$VERSION"
  PAYLOAD_NAME="pnpink_payload_$VERSION.zip"
fi

INSTALLER="$TEMP_DIR/install.py"
PAYLOAD="$TEMP_DIR/$PAYLOAD_NAME"
download "$BASE_URL/install.py" "$INSTALLER"
download "$BASE_URL/$PAYLOAD_NAME" "$PAYLOAD"

if [ -n "${PNPINK_PYTHON:-}" ]; then
  PYTHON=$PNPINK_PYTHON
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "ERROR: Python 3 was not found. Set PNPINK_PYTHON to a Python 3 executable." >&2
  exit 1
fi

"$PYTHON" "$INSTALLER" "$PAYLOAD"
