"""Entry point: runs the collector in the background and the HTTP server up front."""

import logging
import os
import signal
import sqlite3
import threading
import time

from . import collectors, config, db, server

log = logging.getLogger("monitor")

#: How often the retention policy is applied.
PRUNE_EVERY = 3600


def collect_loop(stop: threading.Event) -> None:
    """Take a sample every INTERVAL seconds until signalled to stop."""
    db.init()
    sampler = collectors.Sampler()
    last_prune = 0.0
    # Schedule against a fixed clock so the interval does not drift.
    next_tick = time.monotonic() + config.INTERVAL

    while not stop.is_set():
        if stop.wait(max(0.0, next_tick - time.monotonic())):
            break
        next_tick += config.INTERVAL
        try:
            db.insert_sample(sampler.sample())
        except Exception:
            log.exception("failed to collect a sample")

        if time.monotonic() - last_prune > PRUNE_EVERY:
            last_prune = time.monotonic()
            try:
                removed = db.prune(config.RETENTION_DAYS)
                if removed:
                    log.info("retention: %d samples removed", removed)
            except Exception:
                log.exception("failed to apply retention")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        db.init()
    except sqlite3.OperationalError:
        # Far and away the most common cause: the directory is not writable by
        # the user we run as. Worth saying plainly, since in a container that
        # user is rarely the one who created the directory.
        log.error(
            "cannot open the database at %s — check that its directory exists "
            "and is writable by uid %d",
            config.DB_PATH, os.getuid(),
        )
        raise

    stop = threading.Event()
    collector = threading.Thread(target=collect_loop, args=(stop,), daemon=True)
    collector.start()

    httpd = server.serve()
    log.info(
        "monitor on http://%s:%d (sampling every %ds, database at %s)",
        config.HOST, config.PORT, config.INTERVAL, config.DB_PATH,
    )

    def shutdown(_signum, _frame):
        log.info("shutting down…")
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        httpd.serve_forever()
    finally:
        stop.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
