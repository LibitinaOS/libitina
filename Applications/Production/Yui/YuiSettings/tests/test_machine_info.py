"""Hardware discovery fixtures that also run on non-Linux development hosts."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from machine_info import machine_details


class MachineInfoTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def test_pc_details_and_backing_disk_capacity(self):
        self.write("sys/class/dmi/id/sys_vendor", "Example Inc.\n")
        self.write("sys/class/dmi/id/product_name", "Laptop\n")
        self.write("proc/cpuinfo", "processor : 0\nmodel name : Example CPU\n\n"
                   "processor : 1\nmodel name : Example CPU\n")
        self.write("proc/meminfo", "MemTotal: 16777216 kB\n")
        for name in ("sda", "nvme0n1", "loop0", "dm-0", "md0", "zram0"):
            self.write(f"sys/block/{name}/size", "1953125000\n")
        details = dict(machine_details(self.root))
        self.assertEqual(details, {"Make": "Example Inc.", "Model": "Laptop",
                         "Processor": "Example CPU × 2", "Memory": "16.0 GiB",
                         "Storage": "2.0 TB"})

    def test_device_tree_model_and_arm_processor(self):
        self.write("sys/firmware/devicetree/base/model", "Example Board\x00")
        self.write("proc/cpuinfo", "processor : 0\nHardware : Example SoC\n")
        details = dict(machine_details(self.root))
        self.assertEqual(details["Model"], "Example Board")
        self.assertEqual(details["Processor"], "Example SoC × 1")
        self.assertEqual(details["Make"], "Unknown")

    def test_missing_and_malformed_information(self):
        self.assertTrue(all(value == "Unknown" for _, value in machine_details(self.root)))
        self.write("proc/meminfo", "MemTotal: invalid kB")
        self.write("sys/block/sda/size", "invalid")
        self.assertTrue(all(value == "Unknown" for _, value in machine_details(self.root)))


if __name__ == "__main__":
    unittest.main()
