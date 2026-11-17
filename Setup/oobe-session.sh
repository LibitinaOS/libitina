#!/bin/sh
# Only the temporary account may enter the first-boot session.
set -eu
[ "$(id -un)" = defaultuser0 ] || exit 1
export XDG_SESSION_TYPE=wayland
export XDG_CURRENT_DESKTOP=labwc
export GDK_BACKEND=wayland
# Only a newly finished wizard may request provisioning after a failed attempt.
rm -f "$HOME/.config/os-setup/setup.json"
# --session ties compositor lifetime to the wizard and its background.
labwc --config /usr/share/libitina/Setup/labwc \
    --session '/bin/sh /usr/share/libitina/Setup/oobe-client.sh'
if [ -f "$HOME/.config/os-setup/setup.json" ]; then
    sudo -n /usr/bin/systemctl start --no-block libitina-setup.service
fi
