"""Configuration, read from environment variables."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _csv(name: str) -> list[str]:
    return [s.strip() for s in os.environ.get(name, "").split(",") if s.strip()]


#: Path to the SQLite database.
DB_PATH = Path(os.environ.get("MONITOR_DB") or BASE_DIR / "data" / "monitor.db")
#: Sampling interval, in seconds.
INTERVAL = max(1, _int("MONITOR_INTERVAL", 10))
#: Address and port the HTTP server binds to.
HOST = os.environ.get("MONITOR_HOST", "0.0.0.0")
PORT = _int("MONITOR_PORT", 8787)
#: How many days of history to keep (0 keeps everything).
RETENTION_DAYS = _int("MONITOR_RETENTION_DAYS", 14)
#: Sysfs root holding the temperature sensors.
HWMON_ROOT = os.environ.get("MONITOR_HWMON", "/sys/class/hwmon")
#: Network interfaces to sum; empty means every interface but loopback.
NET_IFACES = _csv("MONITOR_NET_IFACES")
#: Cap on points per series returned by the API (drives the downsampling).
MAX_POINTS = _int("MONITOR_MAX_POINTS", 480)
#: Index of the NVIDIA GPU being monitored.
GPU_INDEX = _int("MONITOR_GPU_INDEX", 0)
