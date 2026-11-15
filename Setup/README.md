# First boot in built images

BuildTool installs Yui's Debian dependencies, NetworkManager, LightDM and its GTK
greeter, and registers the YuiHirasawa session from the installed repository.
It purges ifupdown so it cannot compete with NetworkManager.

LightDM initially autologins the locked, unprivileged `defaultuser0` account into
`libitina-oobe`. This runs Labwc with `--session` pointing only at the wizard.
When the wizard and compositor exit, the session requests the root-owned
`libitina-setup.service` through a single-command sudo rule. Closing the wizard
without exporting settings simply returns to login/OOBE.

The service stops LightDM and the temporary user's processes, validates
`/home/defaultuser0/.config/os-setup/setup.json`, creates an administrator account,
sets its password through chpasswd's standard input, and applies locale, keyboard,
timezone and hostname settings through systemd tools. Keyboard settings use Debian's
`keyboard-configuration` package interface because Debian disables localectl's
keyboard setters. It selects YuiHirasawa for subsequent logins,
deletes the password export and temporary account, removes the OOBE autologin,
session and sudo rule, marks setup complete, and reboots. Normal boots show the
GTK greeter. A failed provisioning service restarts LightDM so setup can be retried;
inspect `journalctl -u libitina-setup` for errors.

The GitHub profile currently supplies the wizard's name and username; importing
its picture into the permanent account is not yet implemented. Network selection
is not yet part of the wizard; wired networks use NetworkManager's automatic DHCP.
The complete boot flow requires testing in a Linux VM with a display device.
