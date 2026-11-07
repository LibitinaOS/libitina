"""Read public Linux hardware information without services or elevated access."""

from pathlib import Path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip("\x00 \n\t")
    except (OSError, UnicodeError):
        return ""


def _capacity(size: int, binary: bool = False) -> str:
    base = 1024 if binary else 1000
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB") if binary else (
        "B", "kB", "MB", "GB", "TB", "PB")
    value = float(size)
    for unit in units:
        if value < base or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= base


def machine_details(root: Path = Path("/")) -> list[tuple[str, str]]:
    """Return display rows; missing or inaccessible hardware stays unknown."""
    dmi = root / "sys/class/dmi/id"
    tree = root / "sys/firmware/devicetree/base"
    make = _read(dmi / "sys_vendor")
    model = _read(dmi / "product_name") or _read(tree / "model")
    cpu_fields: dict[str, list[str]] = {}
    for line in _read(root / "proc/cpuinfo").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            cpu_fields.setdefault(key.strip(), []).append(value.strip())
    cpu = next((values[0] for key in ("model name", "Hardware", "Processor")
                if (values := cpu_fields.get(key)) and values[0]), "")
    processors = len(cpu_fields.get("processor", []))
    if cpu and processors:
        cpu = f"{cpu} × {processors}"
    memory = ""
    for line in _read(root / "proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            try:
                memory = _capacity(int(line.split()[1]) * 1024, binary=True)
            except (ValueError, IndexError):
                pass
            break

    # Whole backing disks only: partitions and device-mapper/RAID views must
    # not count the same capacity again. Include fixed and removable media.
    storage = 0
    try:
        disks = list((root / "sys/block").iterdir())
    except OSError:
        disks = []
    for disk in disks:
        if disk.name.startswith(("loop", "ram", "zram", "dm-", "md")):
            continue
        try:
            # The kernel reports size in 512-byte sectors, even on 4K disks.
            storage += max(0, int(_read(disk / "size"))) * 512
        except ValueError:
            continue
    return [("Make", make or "Unknown"), ("Model", model or "Unknown"),
            ("Processor", cpu or "Unknown"), ("Memory", memory or "Unknown"),
            ("Storage", _capacity(storage) if storage else "Unknown")]
