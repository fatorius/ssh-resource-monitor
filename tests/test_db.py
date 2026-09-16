"""Tests for the persistence and aggregation layer.

    python3 -m unittest discover -s tests -t . -v
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("MONITOR_INTERVAL", "10")

from monitor import config, db  # noqa: E402


def make_sample(ts, **overrides):
    sample = {
        "ts": ts,
        "cpu_pct": 25.0,
        "ram_pct": 40.0,
        "ram_used_bytes": 1000,
        "ram_total_bytes": 4000,
        "cpu_temp_c": 45.0,
        "cpu_core_temps": {"Core 0": 44.0, "Core 1": 46.0},
        "gpu_temp_c": 50.0,
        "gpu_pct": 30.0,
        "vram_pct": 12.5,
        "vram_used_mb": 256.0,
        "vram_total_mb": 2048.0,
        "net_rx_bps": 100.0,
        "net_tx_bps": 200.0,
        "net_rx_total": 10_000,
        "net_tx_total": 20_000,
        "disks": {
            "/": {"total_bytes": 200, "used_bytes": 20, "pct": 10.0},
            "/boot/efi": {"total_bytes": 100, "used_bytes": 1, "pct": 1.0},
        },
    }
    sample.update(overrides)
    return sample


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmp.name) / "test.db"
        # Every test starts with a clean connection pointed at a fresh database.
        db._local.__dict__.clear()
        db.init()

    def tearDown(self):
        db._local.__dict__.clear()
        self._tmp.cleanup()

    def test_stores_and_reads_back_the_latest_sample(self):
        now = int(time.time())
        db.insert_sample(make_sample(now - 10, cpu_pct=10.0))
        db.insert_sample(make_sample(now, cpu_pct=90.0))

        latest = db.latest()
        self.assertEqual(latest["ts"], now)
        self.assertEqual(latest["cpu_pct"], 90.0)
        self.assertEqual(latest["cpu_core_temps"], {"Core 0": 44.0, "Core 1": 46.0})

    def test_latest_is_none_on_an_empty_database(self):
        self.assertIsNone(db.latest())

    def test_a_repeated_timestamp_replaces_instead_of_duplicating(self):
        now = int(time.time())
        db.insert_sample(make_sample(now, cpu_pct=10.0))
        db.insert_sample(make_sample(now, cpu_pct=80.0))

        conn = db.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM core_temps").fetchone()[0], 2)
        self.assertEqual(db.latest()["cpu_pct"], 80.0)

    def test_series_averages_within_a_bucket(self):
        now = int(time.time())
        # Two samples inside the same 60s bucket should collapse into an average.
        base = (now // 60) * 60
        db.insert_sample(make_sample(base, cpu_pct=20.0))
        db.insert_sample(make_sample(base + 10, cpu_pct=40.0))

        result = db.series(3600)
        bucket_index = result["t"].index((base // result["bucket"]) * result["bucket"])
        if result["bucket"] >= 20:
            self.assertAlmostEqual(result["series"]["cpu_pct"][bucket_index], 30.0)
        self.assertEqual(len(result["t"]), len(result["series"]["cpu_pct"]))

    def test_series_aligns_cores_to_the_time_axis(self):
        now = int(time.time())
        for offset in range(0, 60, 10):
            db.insert_sample(make_sample(now - offset))

        result = db.series(3600)
        self.assertEqual(set(result["cores"]), {"Core 0", "Core 1"})
        for values in result["cores"].values():
            self.assertEqual(len(values), len(result["t"]))

    def test_series_is_empty_when_the_window_holds_no_data(self):
        db.insert_sample(make_sample(int(time.time()) - 7 * 86400))
        result = db.series(900)
        self.assertEqual(result["t"], [])
        self.assertEqual(result["cores"], {})

    def test_bucket_grows_with_the_window(self):
        self.assertEqual(db.bucket_for(900), config.INTERVAL)
        self.assertGreater(db.bucket_for(2_592_000), db.bucket_for(3600))

    def test_bucket_respects_the_point_cap(self):
        for span in (900, 3600, 86400, 604800, 2_592_000):
            points = span / db.bucket_for(span)
            self.assertLessEqual(points, config.MAX_POINTS + 1, f"{span}s window")

    def test_retention_drops_old_samples_and_their_cores(self):
        now = int(time.time())
        db.insert_sample(make_sample(now - 40 * 86400))
        db.insert_sample(make_sample(now))

        self.assertEqual(db.prune(14), 1)
        conn = db.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM core_temps").fetchone()[0], 2)

    def test_zero_retention_keeps_everything(self):
        db.insert_sample(make_sample(int(time.time()) - 400 * 86400))
        self.assertEqual(db.prune(0), 0)
        self.assertIsNotNone(db.latest())

    def test_missing_fields_become_nulls(self):
        now = int(time.time())
        db.insert_sample({"ts": now, "cpu_pct": 5.0})
        latest = db.latest()
        self.assertEqual(latest["cpu_pct"], 5.0)
        self.assertIsNone(latest["gpu_temp_c"])
        self.assertEqual(latest["cpu_core_temps"], {})


class DiskTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmp.name) / "test.db"
        db._local.__dict__.clear()
        db.init()

    def tearDown(self):
        db._local.__dict__.clear()
        self._tmp.cleanup()

    def test_stores_and_reads_back_each_filesystem(self):
        now = int(time.time())
        db.insert_sample(make_sample(now))
        disks = db.latest()["disks"]
        self.assertEqual(set(disks), {"/", "/boot/efi"})
        self.assertEqual(disks["/"]["pct"], 10.0)
        self.assertEqual(disks["/"]["total_bytes"], 200)

    def test_series_aligns_disks_to_the_time_axis(self):
        now = int(time.time())
        for offset in range(0, 60, 10):
            db.insert_sample(make_sample(now - offset))
        result = db.series(3600)
        self.assertEqual(set(result["disks"]), {"/", "/boot/efi"})
        for values in result["disks"].values():
            self.assertEqual(len(values), len(result["t"]))

    def test_retention_drops_old_disk_rows(self):
        now = int(time.time())
        db.insert_sample(make_sample(now - 40 * 86400))
        db.insert_sample(make_sample(now))
        db.prune(14)
        conn = db.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM disk_usage").fetchone()[0], 2)

    def test_a_sample_without_disks_is_fine(self):
        now = int(time.time())
        db.insert_sample({"ts": now, "cpu_pct": 5.0})
        self.assertEqual(db.latest()["disks"], {})


class CollectorTests(unittest.TestCase):
    def test_a_sample_carries_every_expected_key(self):
        from monitor import collectors

        sample = collectors.Sampler().sample()
        expected = {
            "ts", "cpu_pct", "ram_pct", "ram_used_bytes", "ram_total_bytes",
            "cpu_temp_c", "cpu_core_temps", "gpu_temp_c", "gpu_pct", "vram_pct",
            "vram_used_mb", "vram_total_mb", "net_rx_bps", "net_tx_bps",
            "net_rx_total", "net_tx_total", "disks",
        }
        self.assertEqual(expected - set(sample), set())

    def test_pseudo_filesystems_are_left_out(self):
        from monitor import collectors

        disks = collectors.read_disks()
        # Snap images are read-only squashfs and permanently 100% full; letting
        # them through would bury the real filesystems.
        self.assertFalse([m for m in disks if m.startswith("/snap/")])
        for usage in disks.values():
            self.assertGreater(usage["total_bytes"], 0)

    def test_network_rates_are_never_negative(self):
        from monitor import collectors

        sampler = collectors.Sampler()
        # Simulate the interface counters being reset.
        sampler._prev_net = (10**12, 10**12)
        sample = sampler.sample()
        self.assertGreaterEqual(sample["net_rx_bps"], 0)
        self.assertGreaterEqual(sample["net_tx_bps"], 0)


if __name__ == "__main__":
    unittest.main()
