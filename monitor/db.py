"""Sample persistence, on SQLite."""

import sqlite3
import threading
import time
from pathlib import Path

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts              INTEGER PRIMARY KEY,
    cpu_pct         REAL,
    ram_pct         REAL,
    ram_used_bytes  INTEGER,
    ram_total_bytes INTEGER,
    cpu_temp_c      REAL,
    gpu_temp_c      REAL,
    gpu_pct         REAL,
    vram_pct        REAL,
    vram_used_mb    REAL,
    vram_total_mb   REAL,
    net_rx_bps      REAL,
    net_tx_bps      REAL,
    net_rx_total    INTEGER,
    net_tx_total    INTEGER
);

CREATE TABLE IF NOT EXISTS core_temps (
    ts      INTEGER NOT NULL,
    core    TEXT NOT NULL,
    temp_c  REAL,
    PRIMARY KEY (ts, core)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS disk_usage (
    ts          INTEGER NOT NULL,
    mount       TEXT NOT NULL,
    total_bytes INTEGER,
    used_bytes  INTEGER,
    pct         REAL,
    PRIMARY KEY (ts, mount)
) WITHOUT ROWID;
"""

#: Time-series columns exposed by the API, in query order.
SERIES_COLUMNS = (
    "cpu_pct",
    "ram_pct",
    "cpu_temp_c",
    "gpu_temp_c",
    "gpu_pct",
    "vram_pct",
    "net_rx_bps",
    "net_tx_bps",
)


def connect() -> sqlite3.Connection:
    """One connection per thread: the collector and the HTTP server each get theirs."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def init() -> None:
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)


def insert_sample(sample: dict) -> None:
    """Store a sample along with the per-core temperatures that came with it."""
    conn = connect()
    cols = [
        "ts", "cpu_pct", "ram_pct", "ram_used_bytes", "ram_total_bytes",
        "cpu_temp_c", "gpu_temp_c", "gpu_pct", "vram_pct", "vram_used_mb",
        "vram_total_mb", "net_rx_bps", "net_tx_bps", "net_rx_total",
        "net_tx_total",
    ]
    placeholders = ",".join("?" for _ in cols)
    with conn:
        conn.execute(
            f"INSERT OR REPLACE INTO samples ({','.join(cols)}) VALUES ({placeholders})",
            [sample.get(c) for c in cols],
        )
        cores = sample.get("cpu_core_temps") or {}
        if cores:
            conn.executemany(
                "INSERT OR REPLACE INTO core_temps (ts, core, temp_c) VALUES (?,?,?)",
                [(sample["ts"], name, value) for name, value in cores.items()],
            )
        disks = sample.get("disks") or {}
        if disks:
            conn.executemany(
                """INSERT OR REPLACE INTO disk_usage
                   (ts, mount, total_bytes, used_bytes, pct) VALUES (?,?,?,?,?)""",
                [
                    (sample["ts"], mount, d["total_bytes"], d["used_bytes"], d["pct"])
                    for mount, d in disks.items()
                ],
            )


def latest() -> dict | None:
    """The most recent sample, with its per-core temperatures attached."""
    conn = connect()
    row = conn.execute("SELECT * FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
    if row is None:
        return None
    out = dict(row)
    cores = conn.execute(
        "SELECT core, temp_c FROM core_temps WHERE ts = ? ORDER BY core", (row["ts"],)
    ).fetchall()
    out["cpu_core_temps"] = {c["core"]: c["temp_c"] for c in cores}
    disks = conn.execute(
        """SELECT mount, total_bytes, used_bytes, pct FROM disk_usage
           WHERE ts = ? ORDER BY mount""",
        (row["ts"],),
    ).fetchall()
    out["disks"] = {
        d["mount"]: {
            "total_bytes": d["total_bytes"],
            "used_bytes": d["used_bytes"],
            "pct": d["pct"],
        }
        for d in disks
    }
    return out


def bucket_for(span_seconds: int) -> int:
    """Aggregation bucket width that keeps a window within MAX_POINTS points."""
    raw = span_seconds / max(1, config.MAX_POINTS)
    return max(config.INTERVAL, int(raw // config.INTERVAL) * config.INTERVAL or config.INTERVAL)


def series(span_seconds: int) -> dict:
    """Bucket-aggregated series for the requested window."""
    conn = connect()
    now = int(time.time())
    since = now - span_seconds
    bucket = bucket_for(span_seconds)

    aggregates = ",".join(f"AVG({c}) AS {c}" for c in SERIES_COLUMNS)
    rows = conn.execute(
        f"""SELECT (ts / ?) * ? AS b, {aggregates}
            FROM samples WHERE ts >= ?
            GROUP BY b ORDER BY b""",
        (bucket, bucket, since),
    ).fetchall()

    timestamps = [r["b"] for r in rows]
    index = {t: i for i, t in enumerate(timestamps)}
    data = {c: [r[c] for r in rows] for c in SERIES_COLUMNS}

    # Per-core temperatures become one series per core, on the same time axis.
    cores: dict[str, list[float | None]] = {}
    core_rows = conn.execute(
        """SELECT (ts / ?) * ? AS b, core, AVG(temp_c) AS temp_c
           FROM core_temps WHERE ts >= ?
           GROUP BY b, core ORDER BY core, b""",
        (bucket, bucket, since),
    ).fetchall()
    for r in core_rows:
        pos = index.get(r["b"])
        if pos is None:
            continue
        cores.setdefault(r["core"], [None] * len(timestamps))[pos] = r["temp_c"]

    # Disk usage becomes one series per mountpoint, on that same axis.
    disks: dict[str, list[float | None]] = {}
    disk_rows = conn.execute(
        """SELECT (ts / ?) * ? AS b, mount, AVG(pct) AS pct
           FROM disk_usage WHERE ts >= ?
           GROUP BY b, mount ORDER BY mount, b""",
        (bucket, bucket, since),
    ).fetchall()
    for r in disk_rows:
        pos = index.get(r["b"])
        if pos is None:
            continue
        disks.setdefault(r["mount"], [None] * len(timestamps))[pos] = r["pct"]

    return {
        "from": since,
        "to": now,
        "bucket": bucket,
        "t": timestamps,
        "series": data,
        "cores": cores,
        "disks": disks,
    }


def prune(retention_days: int) -> int:
    """Drop samples older than the retention window. Returns how many went."""
    if retention_days <= 0:
        return 0
    cutoff = int(time.time()) - retention_days * 86400
    conn = connect()
    with conn:
        deleted = conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,)).rowcount
        conn.execute("DELETE FROM core_temps WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM disk_usage WHERE ts < ?", (cutoff,))
    return deleted
