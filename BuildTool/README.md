# Building images

Run on a Linux amd64 or arm64 host as root. The default target remains amd64.
ZIP, raw IMG and QCOW2 outputs support `--arch amd64` and `--arch arm64`.

On Debian, install the common host tools:

```sh
sudo apt install python3 git debootstrap coreutils mount util-linux parted udev e2fsprogs dosfstools qemu-utils
```

To cross-build on Debian Trixie, also enable QEMU user emulation:

```sh
sudo apt install qemu-user qemu-user-binfmt
sudo systemctl restart systemd-binfmt
```

Older Debian releases use `qemu-user-static` and `binfmt-support`. The builder
requires an enabled `qemu-aarch64` (or `qemu-x86_64` for the reverse direction)
handler under `/proc/sys/fs/binfmt_misc`, including the `F` flag. It checks this
before creating images. It does not change host binfmt registrations itself.
Containers need binfmt configured by the host and privileges for mount namespaces,
loop devices, and filesystem mounts.

```sh
sudo python3 BuildTool/main.py qcow2 ./libitina-arm64.qcow2 --arch arm64
sudo python3 BuildTool/main.py img ./libitina-arm64.img --arch arm64
sudo python3 BuildTool/main.py zip ./libitina-arm64.zip --arch arm64
```

Cross builds use debootstrap's foreign stage followed by an emulated second stage.
Registered QEMU interpreters handle subsequent guest executables, including package
maintainer scripts. No emulator is copied into the guest. Native builds do not
require QEMU user emulation. Guest packages are selected for the target architecture.

Disk images use GPT and UEFI with the architecture's fallback loader:
`EFI/BOOT/BOOTX64.EFI` on amd64, `EFI/BOOT/BOOTAA64.EFI` on arm64.
ARM64 images target generic UEFI machines/VMs (for example QEMU virt with AAVMF),
not device-specific phone or Raspberry Pi boot firmware.
Use ARM64 firmware and virtual hardware when booting an ARM64 image.

Build storage defaults to `/var/tmp`, ignoring TMPDIR. Select disk-backed storage
with `--work-dir PATH`; `/tmp` and RAM filesystems are rejected for storage and
output unless `--force-tmp` is explicit. That override uses normal Python temporary
directory selection when `--work-dir` is omitted. The raw disk defaults to 16 GiB,
QCOW2 to 20 GiB, and the root partition to 10 GiB. Allow space for both the raw
staging image and final output during QCOW2 conversion.
