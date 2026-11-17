# DeNura

Component to rebrand the operating system's os-release files under Project Libitina.

Rebranding also updates existing `/etc/issue` and `/etc/issue.net` login banners
to the same display name as os-release, such as `LibitinaOS (Debian 13)`.
The local banner retains agetty's hostname (`\n`) and terminal
(`\l`) placeholders, including on serial consoles. The hostname itself is unchanged.
Each run backs up these banners alongside os-release; `--revert` restores the
banners associated with the selected os-release backup when available.
