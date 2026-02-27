#!/bin/sh
# install.sh — PnPInk installer (Linux + macOS). No args required.

set -eu

fail() { echo "ERROR: $*" 1>&2; exit 1; }
info() { echo "$*"; }

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

MANIFEST="$SCRIPT_DIR/manifest.json"
[ -f "$MANIFEST" ] || fail "manifest.json not found next to installer: $MANIFEST"

# Minimal JSON read without jq (expects simple JSON with double quotes)
json_get() {
  # $1 = key
  # prints value or empty
  key="$1"
  # shellcheck disable=SC2002
  cat "$MANIFEST" | tr -d '\r\n' | sed -n "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" | head -n 1
}

NAME=$(json_get name)
VERSION=$(json_get version)
CHANNEL=$(json_get channel | tr '[:upper:]' '[:lower:]')

[ -n "$NAME" ] || fail "manifest.json missing 'name'"
[ -n "$VERSION" ] || fail "manifest.json missing 'version'"
[ "$CHANNEL" = "main" ] || [ "$CHANNEL" = "dev" ] || fail "manifest.json 'channel' must be 'main' or 'dev'"

# Find payload zip (newest .zip in same folder)
ZIP=$(ls -1t "$SCRIPT_DIR"/*.zip 2>/dev/null | head -n 1 || true)
[ -n "$ZIP" ] || fail "No .zip payload found next to installer."

vsafe() {
  # keep [0-9A-Za-z.-], convert others to _, then dots to underscores
  echo "$1" | sed 's/[^0-9A-Za-z\.\-]/_/g; s/\./_/g'
}

# -------- Inkscape discovery --------
ink_from_process_linux() {
  pid=$(pgrep -x inkscape 2>/dev/null | head -n 1 || true)
  [ -n "$pid" ] || return 1
  if [ -r "/proc/$pid/exe" ]; then
    readlink "/proc/$pid/exe" 2>/dev/null || true
  else
    return 1
  fi
}

ink_from_process_macos() {
  pid=$(pgrep -x inkscape 2>/dev/null | head -n 1 || true)
  [ -n "$pid" ] || return 1
  # macOS does not have /proc by default; fall back to PATH if running.
  command -v inkscape 2>/dev/null || true
}

ink_from_path() {
  command -v inkscape 2>/dev/null || true
}

ink_from_typical() {
  # common macOS bundle path + a couple of variants
  for c in \
    /Applications/Inkscape.app/Contents/MacOS/inkscape \
    /Applications/Inkscape.app/Contents/Resources/bin/inkscape \
    "$HOME/Applications/Inkscape.app/Contents/MacOS/inkscape" \
    "$HOME/Applications/Inkscape.app/Contents/Resources/bin/inkscape"
  do
    [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

wait_for_inkscape() {
  info "Inkscape not found. Please open Inkscape now."
  info "Waiting for inkscape process... (Ctrl-C to cancel)"
  while :; do
    INK=""
    if [ "$(uname -s)" = "Darwin" ]; then
      INK=$(ink_from_process_macos || true)
    else
      INK=$(ink_from_process_linux || true)
    fi
    [ -n "$INK" ] && { echo "$INK"; return 0; }
    sleep 0.6
  done
}

INK=""
# 1) process
if [ "$(uname -s)" = "Darwin" ]; then
  INK=$(ink_from_process_macos || true)
else
  INK=$(ink_from_process_linux || true)
fi
# 2) PATH
[ -n "$INK" ] || INK=$(ink_from_path || true)
# 3) typical
[ -n "$INK" ] || INK=$(ink_from_typical || true)
# 4) wait
[ -n "$INK" ] || INK=$(wait_for_inkscape)

[ -n "$INK" ] || fail "Unable to locate inkscape executable."

info "Using Inkscape: $INK"

USER_DIR=$("$INK" --user-data-directory 2>/dev/null | head -n 1 | tr -d '\r')
[ -n "$USER_DIR" ] || fail "Inkscape returned empty --user-data-directory"

EXT_DIR="$USER_DIR/extensions"
mkdir -p "$EXT_DIR"

info "Inkscape user dir: $USER_DIR"
info "Extensions dir:    $EXT_DIR"
info "Payload zip:       $ZIP"

# Extract to temp
TMP="$(mktemp -d 2>/dev/null || mktemp -d -t pnpink_install)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT INT TERM

unzip -q "$ZIP" -d "$TMP"

# Locate payload root containing inx/
PAYLOAD_ROOT=$(find "$TMP" -type d -name inx -print 2>/dev/null | head -n 1 || true)
[ -n "$PAYLOAD_ROOT" ] || fail "Could not find an 'inx' folder inside zip."
PAYLOAD_ROOT=$(cd "$PAYLOAD_ROOT/.." && pwd)

INX_SRC="$PAYLOAD_ROOT/inx"
[ -d "$INX_SRC" ] || fail "Missing inx folder at $INX_SRC"

VSAFE=$(vsafe "$VERSION")
if [ "$CHANNEL" = "dev" ]; then
  DST_FOLDER_NAME="pnpink_dev_$VSAFE"
else
  DST_FOLDER_NAME="pnpink"
fi
DST_FOLDER="$EXT_DIR/$DST_FOLDER_NAME"

# Install code folder
if [ "$CHANNEL" = "main" ] && [ -d "$DST_FOLDER" ]; then
  info "Removing previous main installation: $DST_FOLDER"
  rm -rf "$DST_FOLDER"
fi
mkdir -p "$DST_FOLDER"

info "Copying payload to: $DST_FOLDER"
# copy everything except inx/
( cd "$PAYLOAD_ROOT" && tar cf - --exclude=./inx . ) | ( cd "$DST_FOLDER" && tar xpf - )

# sed in-place portability (GNU vs BSD)
sedi() {
  # usage: sedi 's/old/new/' file
  if [ "$(uname -s)" = "Darwin" ]; then
    sed -i '' "$1" "$2"
  else
    sed -i "$1" "$2"
  fi
}

patch_inx() {
  file="$1"
  tool="$2"

  if [ "$CHANNEL" = "dev" ]; then
    ID_PREFIX="pnpink_dev_$VSAFE"
    SUBMENU="PnPInk DEV v$VERSION"
    CMD_FOLDER="$DST_FOLDER_NAME"
  else
    ID_PREFIX="pnpink"
    SUBMENU="PnPInk v$VERSION"
    CMD_FOLDER="pnpink"
  fi

  NEW_ID="$ID_PREFIX.$tool"
  NEW_CMD="$CMD_FOLDER/$tool.py"

  # Replace <id>...</id>
  sedi "s#<id>[^<]*</id>#<id>$NEW_ID</id>#g" "$file"
  # Replace submenu _name="..."
  sedi "s#<submenu\\b\\([^>]*\\)_name=\"[^\"]*\"\\([^>]*/>\\)#<submenu\\1_name=\"$SUBMENU\"\\2#g" "$file"
  # Replace command body
  sedi "s#\\(<command\\b[^>]*>\\)[^<]*\\(</command>\\)#\\1$NEW_CMD\\2#g" "$file"
}

# Copy and patch INX into extensions root
INX_FILES=$(ls "$INX_SRC"/*.inx 2>/dev/null || true)
[ -n "$INX_FILES" ] || fail "No .inx files found in payload/inx"

info "Installing INX into: $EXT_DIR"
for f in $INX_FILES; do
  base=$(basename "$f")
  tool=${base%.inx}
  dst="$EXT_DIR/$base"
  cp -f "$f" "$dst"
  patch_inx "$dst" "$tool"
  info "Patched: $base  (tool=$tool)"
done

info ""
info "Installed $NAME ($CHANNEL) v$VERSION."
if pgrep -x inkscape >/dev/null 2>&1; then
  info "Inkscape is currently running. Close and re-open Inkscape for PnPInk to appear in Extensions menu."
else
  info "Start (or re-start) Inkscape for PnPInk to appear in Extensions menu."
fi
info "Done."
