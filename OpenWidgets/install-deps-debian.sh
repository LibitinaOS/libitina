#!/usr/bin/env bash
# Install OpenWidgets dependencies using only APT on Debian 13 or newer.
# Run as your normal user: bash install-deps-debian.sh
set -euo pipefail

trap 'printf "Installation failed at line %s. See the error above.\n" "$LINENO" >&2' ERR

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$project_dir"
if [[ ! -f requirements.txt || ! -f nws-ez/pyproject.toml ]]; then
    printf 'Keep this script in the OpenWidgets project directory.\n' >&2
    exit 1
fi

sudo_cmd=()
if (( EUID != 0 )); then
    if ! command -v sudo >/dev/null 2>&1; then
        printf 'Install sudo or run this script as root.\n' >&2
        exit 1
    fi
    sudo_cmd=(sudo)
fi

# APT resolves Qt's native library dependencies automatically.
# https://packages.debian.org/trixie/python3-pyside6.qtwebenginewidgets
packages=(
    python3 python3-flask python3-requests ca-certificates
    python3-pyside6.qtcore
    python3-pyside6.qtgui
    python3-pyside6.qtwidgets
    python3-pyside6.qtwebenginecore
    python3-pyside6.qtwebenginewidgets
    qt6-wayland libxcb-cursor0 fonts-dejavu-core
)

printf 'Installing Debian system dependencies...\n'
"${sudo_cmd[@]}" apt-get update
"${sudo_cmd[@]}" apt-get install -y --no-install-recommends "${packages[@]}"

# Use Debian's Python even when the caller has a virtual environment active.
# main.py already loads the bundled nws-ez source without installing it.
printf 'Checking Python imports...\n'
/usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0, "nws-ez")
import flask
import requests
import nws_ez
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
PY

printf '\nDependencies installed. From this directory, run in a desktop session:\n'
printf '  /usr/bin/python3 run_no_debug.py\n'
printf 'The Windows taskbar helpers are Windows-only.\n'
