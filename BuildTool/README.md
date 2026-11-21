# Building images

BuildTool runs DeNura inside the guest to rebrand its OS release as LibitinaOS.
DeNura also brands the TTY and serial console login banners as LibitinaOS.
It also runs `Applications/install.py --system`, installing all complete bundled
applications under `/usr/share/libitina/applications` with desktop entries in
`/usr/share/applications`, available to every user. Application selection and
shared backend files follow the application's existing installer.

Both bundled Deletescape UI GTK themes are installed globally in `/usr/share/themes`.
The light `DeletescapeUi` theme is the default through GTK settings and GSettings.
Skeleton home settings apply it to `defaultuser0` and accounts created by setup.

Run on a Linux amd64 or arm64 host as root. The default target remains amd64.
ZIP, raw IMG and QCOW2 outputs support `--arch amd64` and `--arch arm64`.
Completed output files belong to the user and group that invoked `sudo`, with
permissions `0644` (owner read/write, everyone else read). When run directly as
root, output files remain owned by root.

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
QCOW2 to 20 GiB, and the root partition to 10 GiB. Use `--size` to set the
disk capacity in GiB for either image format. Allow space for both the raw
staging image and final output during QCOW2 conversion.

## Semi-immutable disk images

Pass `--rootless` with `img` or `qcow2` to mount the system partition read-only and
use a persistent OverlayFS root. Install `fdisk` on a Debian build host for its
`sfdisk` command.

```sh
sudo apt install fdisk
sudo python3 BuildTool/main.py qcow2 ./libitina-rootless.qcow2 --rootless
```

At boot, the initramfs mounts the home partition and uses it for both the
overlay's writable layer and `/home`. System changes such as edits to `/etc`
and files in `/home` survive reboot. A fresh home volume restores the initial
setup account and launches first-boot setup. GRUB loads the kernel and initramfs
from the read-only system partition, so boot artifact updates require updating
the base image offline. `--rootless` can be combined with `--recovery`.

### Offline factory reset

Resetting the home partition erases all user files and system changes stored
there. Boot another Linux system with `systemd-repart` and `mkfs.ext4` installed.
Identify the Libitina disk with `lsblk`, then mount its system partition (the
second partition) read-only at `/mnt/libitina`. Copy the reset definition and
unmount all partitions on the Libitina disk before running `systemd-repart`.
Substitute the whole Libitina disk for `/dev/sdX` below.

```sh
repart_defs=$(mktemp -d)
cp /mnt/libitina/usr/lib/libitina/repart.d/10-home.conf "$repart_defs/"
sudo umount /mnt/libitina
sudo systemd-repart --definitions="$repart_defs" --factory-reset=yes /dev/sdX
```

Check that the preview targets only `DEBHOME`, then apply the reset. The system,
EFI, and optional RecoveryOS partitions are retained.

```sh
sudo systemd-repart --definitions="$repart_defs" --factory-reset=yes --dry-run=no /dev/sdX
```

## RecoveryOS

Install `squashfs-tools` on the build host, then pass `--recovery`:

```sh
sudo apt install squashfs-tools
sudo python3 BuildTool/main.py qcow2 ./libitina.qcow2 --recovery --hostname libitina
sudo python3 BuildTool/main.py zip ./recovery.zip --recovery --hostname libitina
```

IMG and QCOW2 builds reserve 2 GiB for a separate RecoveryOS partition. Select
**Libitina RecoveryOS** from the GRUB menu. The recovery kernel and initramfs
are stored in the main system's `/boot/recovery` directory. The recovery
partition comes before `/home`, so `/home` can still expand on a flashed IMG.
These builds download the Debian base once and copy it locally before installing
each system's packages.

A ZIP build with `--recovery` contains the standalone RecoveryOS
`recovery.squashfs`, `vmlinuz`, and `initrd.img`. To boot it with your own
bootloader, place the SquashFS image on a dedicated partition and boot the
included kernel and initramfs with `root=PARTUUID=<partition-uuid> rootfstype=squashfs ro`.

RecoveryOS uses the configured hostname and opens Foot in a root labwc
session. Its SquashFS base is read-only. The live root is writable through a
RAM-backed overlay, so changes made there disappear on reboot.
