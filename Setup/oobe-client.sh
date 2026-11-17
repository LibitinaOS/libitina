#!/bin/sh
# Run inside Labwc, with the background's lifetime tied to the wizard.
set -eu
setup_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
/usr/bin/python3 "$setup_dir/wallpaper.py" &
background_pid=$!
cleanup() {
    kill "$background_pid" 2>/dev/null || true
    wait "$background_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
/usr/bin/python3 "$setup_dir/main.py"
