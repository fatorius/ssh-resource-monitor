# ssh-resource-monitor

Samples host metrics every 10 seconds, stores them in SQLite, and serves a
dashboard with live indicators and time-series charts.

No front-end dependencies: the charts are SVG drawn in plain JavaScript, so the
dashboard works on a network with no route to the internet. The only Python
dependency is `psutil`.

## Metrics

| Metric | Source |
| --- | --- |
| CPU usage % | `psutil` (`/proc/stat`) |
| RAM usage % | `psutil` (`/proc/meminfo`) |
| CPU core temperatures | sysfs — `/sys/class/hwmon` (`coretemp`, `k10temp`, …) |
| GPU temperature | `nvidia-smi` |
| GPU usage % | `nvidia-smi` (`utilization.gpu`) |
| VRAM usage % | `nvidia-smi` (`memory.used` ÷ `memory.total`) |
| Network throughput | `psutil`, derived from the per-interface counters |

The GPU metrics need `nvidia-smi`. Without it the app keeps running and the GPU
panels simply stay empty.

Note that VRAM usage here is memory *occupancy*, not `nvidia-smi`'s
`utilization.memory`, which reports memory-bandwidth utilisation instead.

## Running it

### Docker (recommended)

```bash
docker compose up -d --build
```

The dashboard is then at `http://<host-ip>:8787`.

`restart: unless-stopped` brings the container back on every boot, as long as
Docker itself is enabled:

```bash
sudo systemctl enable --now docker
```

The container runs as uid/gid 1000 rather than root, so it holds no privileges
it does not need and the SQLite file under `./data` ends up owned by a normal
user. If your host account is not 1000, export `MONITOR_UID` and `MONITOR_GID`
before bringing it up, or the app will not be able to create its database:

```bash
MONITOR_UID=$(id -u) MONITOR_GID=$(id -g) docker compose up -d --build
```

The compose file uses `network_mode: host` and `pid: host` so the readings come
from the host rather than from inside the container. GPU metrics additionally
need the [NVIDIA Container Toolkit](https://github.com/NVIDIA/nvidia-container-toolkit);
without it, comment out the `deploy:` block in `docker-compose.yml`.

#### Keeping GPU access alive

On a host using cgroup v2, `systemctl daemon-reload` — which **any** package
upgrade triggers, including an unattended one — resets the device cgroup of an
already-running container. `nvidia-smi` inside it then fails with
`Failed to initialize NVML: Unknown Error` while the host keeps reading the GPU
normally, and the container cannot notice or recover on its own. The GPU panels
simply go blank until someone restarts it.

`watchdog/monitor-gpu-watchdog` closes that hole. It tests actual GPU access
from inside the container and restarts it only when access was lost but the host
can still see the GPU, so a genuinely absent or broken GPU never sends it into a
restart loop. Two triggers share the one script: an apt hook, which catches the
common cause within seconds of the upgrade, and a one-minute timer for
device-cgroup resets from anywhere else.

```bash
sudo install -m 755 watchdog/monitor-gpu-watchdog /usr/local/bin/
sudo install -m 644 systemd/monitor-gpu-watchdog.{service,timer} /etc/systemd/system/
sudo install -m 644 apt/99-ssh-resource-monitor /etc/apt/apt.conf.d/
sudo systemctl daemon-reload && sudo systemctl enable --now monitor-gpu-watchdog.timer
```

Recovery costs one missed sample: measured at 12 seconds from restart to the
first GPU reading. Running natively under systemd avoids the problem entirely,
since `nvidia-smi` is then an ordinary host process.

### Natively, under systemd

```bash
pip install -r requirements.txt
sudo cp systemd/monitor.service /etc/systemd/system/
sudo systemctl enable --now monitor
```

Adjust `User=` and the paths in the unit file to match your installation.

### Directly, for development

```bash
MONITOR_PORT=8787 MONITOR_INTERVAL=5 python3 -m monitor
```

## Configuration

Everything is an environment variable:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MONITOR_HOST` | `0.0.0.0` | Bind address |
| `MONITOR_PORT` | `8787` | HTTP port |
| `MONITOR_INTERVAL` | `10` | Sampling interval, in seconds |
| `MONITOR_DB` | `./data/monitor.db` | SQLite database path |
| `MONITOR_RETENTION_DAYS` | `14` | Days of history; `0` keeps everything |
| `MONITOR_MAX_POINTS` | `480` | Cap on points per series (drives downsampling) |
| `MONITOR_NET_IFACES` | every one but `lo` | Comma-separated interfaces to sum |
| `MONITOR_HWMON` | `/sys/class/hwmon` | Sysfs root for the temperature sensors |
| `MONITOR_GPU_INDEX` | `0` | Which NVIDIA GPU to monitor |

## API

| Route | Returns |
| --- | --- |
| `GET /` | The HTML dashboard |
| `GET /api/current` | Latest sample plus host facts |
| `GET /api/series?range=1h` | Aggregated series. `range`: `15m`, `1h`, `6h`, `24h`, `7d`, `30d` |
| `GET /api/health` | Health check |

Series are bucket-aggregated inside SQLite, so a 30-day window returns about as
many points as a 15-minute one.

## Tests

```bash
python3 -m unittest discover -s tests -t . -v   # persistence, aggregation, collection
gjs tests/test_app.js                           # dashboard scales and formatters
```

The JavaScript tests run under `gjs` (SpiderMonkey), which ships with most
GNOME desktops, so they need no Node installation.

## Layout

```
monitor/
  config.py       configuration from environment variables
  collectors.py   CPU, RAM, sensor, GPU and network readers
  db.py           SQLite schema, writes, aggregation and retention
  server.py       HTTP server: static files + JSON API
  __main__.py     collector thread + server
  web/            dashboard (HTML, CSS, JS)
tests/            Python and JavaScript tests
watchdog/         restores the container's GPU access after a cgroup reset
systemd/          unit files: the native service and the watchdog timer
apt/              apt hook that runs the watchdog after every upgrade
```

## Storage

One sample every 10 seconds is roughly 8,640 rows a day. At the default
14-day retention the database settles in the low tens of MB.

## License

MIT — see [LICENSE](LICENSE).
