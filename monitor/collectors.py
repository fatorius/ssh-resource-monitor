"""Host metric readers."""

import glob
import os
import shutil
import subprocess
import time

import psutil

from . import config

#: hwmon chips that stand for the CPU, in order of preference.
_CPU_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")


def _read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def read_hwmon() -> dict[str, list[tuple[str, float]]]:
    """Every temperature exposed through sysfs, grouped by chip."""
    chips: dict[str, list[tuple[str, float]]] = {}
    for chip_dir in sorted(glob.glob(os.path.join(config.HWMON_ROOT, "hwmon*"))):
        name = _read(os.path.join(chip_dir, "name"))
        if not name:
            continue
        entries: list[tuple[str, float]] = []
        for input_path in sorted(glob.glob(os.path.join(chip_dir, "temp*_input"))):
            base = input_path[: -len("_input")]
            raw = _read(input_path)
            if raw is None:
                continue
            try:
                celsius = int(raw) / 1000.0
            except ValueError:
                continue
            label = _read(base + "_label") or os.path.basename(base)
            entries.append((label, celsius))
        if entries:
            chips.setdefault(name, []).extend(entries)
    return chips


def read_cpu_temps() -> tuple[float | None, dict[str, float]]:
    """(overall CPU temperature, {core: temperature}).

    Prefers the package reading as the overall figure; without one, falls back
    to the hottest core.
    """
    chips = read_hwmon()
    entries: list[tuple[str, float]] = []
    for chip in _CPU_CHIPS:
        if chips.get(chip):
            entries = chips[chip]
            break
    if not entries:
        return None, {}

    cores = {
        label: value
        for label, value in entries
        if label.lower().startswith(("core", "tccd"))
    }
    package = next(
        (v for label, v in entries if label.lower().startswith(("package", "tctl", "tdie"))),
        None,
    )
    if package is None:
        package = max(cores.values()) if cores else max(v for _, v in entries)
    return package, cores


_GPU_QUERY = "temperature.gpu,utilization.gpu,memory.used,memory.total"


def read_gpu() -> dict[str, float | None]:
    """NVIDIA GPU metrics through nvidia-smi; all None when unavailable."""
    empty = {
        "gpu_temp_c": None,
        "gpu_pct": None,
        "vram_pct": None,
        "vram_used_mb": None,
        "vram_total_mb": None,
    }
    if not shutil.which("nvidia-smi"):
        return empty
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                f"--id={config.GPU_INDEX}",
                f"--query-gpu={_GPU_QUERY}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return empty

    parts = [p.strip() for p in out.split(",")]
    if len(parts) != 4:
        return empty
    try:
        temp, util, used, total = (float(p) for p in parts)
    except ValueError:
        return empty
    return {
        "gpu_temp_c": temp,
        "gpu_pct": util,
        "vram_pct": (used / total * 100.0) if total else None,
        "vram_used_mb": used,
        "vram_total_mb": total,
    }


def _net_counters() -> tuple[int, int]:
    """Bytes received and sent, summed over the monitored interfaces."""
    counters = psutil.net_io_counters(pernic=True)
    if config.NET_IFACES:
        selected = [v for k, v in counters.items() if k in config.NET_IFACES]
    else:
        selected = [v for k, v in counters.items() if k != "lo"]
    return sum(c.bytes_recv for c in selected), sum(c.bytes_sent for c in selected)


#: Block devices that are not real storage, or that mirror something already
#: counted.
_SKIP_DEVICE_PREFIXES = ("loop", "ram", "sr", "fd", "zram")


def _whole_disks(names: list[str]) -> list[str]:
    """Drop partitions, keeping only whole disks.

    A partition's name extends its disk's ("sda2" extends "sda", "nvme0n1p1"
    extends "nvme0n1"), and the kernel counts its I/O against both, so summing
    everything would double it.
    """
    candidates = [n for n in names if not n.startswith(_SKIP_DEVICE_PREFIXES)]
    return [
        name for name in candidates
        if not any(other != name and name.startswith(other) for other in candidates)
    ]


def _disk_io_counters() -> tuple[int, int]:
    """Bytes read and written, summed over the monitored block devices."""
    counters = psutil.disk_io_counters(perdisk=True)
    if not counters:
        return 0, 0
    if config.DISK_DEVICES:
        selected = [v for k, v in counters.items() if k in config.DISK_DEVICES]
    else:
        wanted = set(_whole_disks(list(counters)))
        selected = [v for k, v in counters.items() if k in wanted]
    return (
        sum(c.read_bytes for c in selected),
        sum(c.write_bytes for c in selected),
    )


class Sampler:
    """Produces samples, holding the state the rate calculations need."""

    def __init__(self) -> None:
        # psutil.cpu_percent and the network rates are deltas: the first read
        # only establishes the baseline.
        psutil.cpu_percent(interval=None)
        self._prev_net = _net_counters()
        self._prev_disk_io = _disk_io_counters()
        self._prev_time = time.monotonic()

    def sample(self) -> dict:
        now_mono = time.monotonic()
        rx_total, tx_total = _net_counters()
        read_total, write_total = _disk_io_counters()
        elapsed = now_mono - self._prev_time
        if elapsed > 0:
            prev_rx, prev_tx = self._prev_net
            prev_read, prev_write = self._prev_disk_io
            # max(0, ...) guards against a counter reset.
            rx_bps = max(0.0, (rx_total - prev_rx) / elapsed)
            tx_bps = max(0.0, (tx_total - prev_tx) / elapsed)
            read_bps = max(0.0, (read_total - prev_read) / elapsed)
            write_bps = max(0.0, (write_total - prev_write) / elapsed)
        else:
            rx_bps = tx_bps = read_bps = write_bps = 0.0
        self._prev_net = (rx_total, tx_total)
        self._prev_disk_io = (read_total, write_total)
        self._prev_time = now_mono

        memory = psutil.virtual_memory()
        cpu_temp, core_temps = read_cpu_temps()

        return {
            "ts": int(time.time()),
            "cpu_pct": psutil.cpu_percent(interval=None),
            "ram_pct": memory.percent,
            "ram_used_bytes": memory.used,
            "ram_total_bytes": memory.total,
            "cpu_temp_c": cpu_temp,
            "cpu_core_temps": core_temps,
            "net_rx_bps": rx_bps,
            "net_tx_bps": tx_bps,
            "net_rx_total": rx_total,
            "net_tx_total": tx_total,
            "disk_read_bps": read_bps,
            "disk_write_bps": write_bps,
            "disk_read_total": read_total,
            "disk_write_total": write_total,
            "disks": read_disks(),
            **read_gpu(),
        }


def host_info() -> dict:
    """Static host facts, for the dashboard header."""
    gpu_name = None
    if shutil.which("nvidia-smi"):
        try:
            gpu_name = subprocess.run(
                ["nvidia-smi", f"--id={config.GPU_INDEX}",
                 "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=8, check=True,
            ).stdout.strip() or None
        except (subprocess.SubprocessError, OSError):
            gpu_name = None
    return {
        "hostname": os.uname().nodename,
        "cpu_count": psutil.cpu_count(logical=True),
        "gpu_name": gpu_name,
        "boot_time": int(psutil.boot_time()),
        "interval": config.INTERVAL,
    }


#: Pseudo-filesystems that report sizes but hold no real storage.
_PSEUDO_FSTYPES = frozenset({
    "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs", "debugfs",
    "devpts", "devtmpfs", "efivarfs", "fuse.gvfsd-fuse", "fuse.portal",
    "fusectl", "hugetlbfs", "mqueue", "nsfs", "overlay", "proc", "pstore",
    "ramfs", "rpc_pipefs", "securityfs", "squashfs", "sysfs", "tmpfs",
    "tracefs",
})


def _host_mounts() -> list[tuple[str, str, str]]:
    """(device, mountpoint, fstype) for the host's filesystems.

    Inside a container the process's own /proc/mounts describes the container,
    so when a host root is configured we read PID 1's mount table instead —
    which `pid: host` makes the host's.
    """
    source = "/proc/1/mounts" if config.DISK_ROOT else "/proc/self/mounts"
    try:
        with open(source) as fh:
            lines = fh.readlines()
    except OSError:
        return [(p.device, p.mountpoint, p.fstype) for p in psutil.disk_partitions(all=False)]

    mounts = []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mountpoint, fstype = parts[0], parts[1], parts[2]
        # Mountpoints are escaped in octal (a space is \040, and so on).
        mountpoint = mountpoint.encode().decode("unicode_escape")
        mounts.append((device, mountpoint, fstype))
    return mounts


def read_disks() -> dict[str, dict[str, float]]:
    """{mountpoint: {total_bytes, used_bytes, pct}} for each real filesystem.

    Snap images and the other pseudo-filesystems are left out: they are either
    read-only and permanently 100% full, or they hold nothing.
    """
    disks: dict[str, dict[str, float]] = {}
    seen_devices: set[str] = set()

    for device, mountpoint, fstype in sorted(_host_mounts(), key=lambda m: m[1]):
        if config.DISK_MOUNTS:
            if mountpoint not in config.DISK_MOUNTS:
                continue
        elif fstype in _PSEUDO_FSTYPES or device.startswith("/dev/loop"):
            continue
        # The same device mounted twice (a bind, or a btrfs subvolume) is one
        # pool of storage; the shortest path wins because we walk them sorted.
        if device in seen_devices:
            continue

        path = (config.DISK_ROOT + mountpoint) if config.DISK_ROOT else mountpoint
        try:
            usage = psutil.disk_usage(path)
        except (OSError, PermissionError):
            continue
        if usage.total <= 0:
            continue

        seen_devices.add(device)
        disks[mountpoint] = {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "pct": usage.percent,
        }
    return disks
