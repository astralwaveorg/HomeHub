#!/usr/bin/env python3
"""Homehub Orbit collector: dependency-free host, Docker and probe agent.

Runs beside the portal as the `collector` compose service. Host counters come
from /proc, a bind-mounted /sys and read-only volume mounts; container metrics
come from the local Docker socket with one-shot reads. A small SQLite store
keeps 1-minute rollups for dashboard history. The HTTP port is only reachable
on the compose-internal network; the portal proxies and authenticates nothing
here because the metrics themselves are public inside the LAN.
"""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / 'VERSION').read_text().strip() if (ROOT / 'VERSION').is_file() else '1.0.0'
PROC = Path('/proc')
SYS = Path(os.environ.get('HOMEHUB_COLLECTOR_SYS', '/host/sys'))
ROOTFS = Path(os.environ.get('HOMEHUB_COLLECTOR_ROOTFS', '/host/root'))
DOCKER_SOCK = os.environ.get('HOMEHUB_COLLECTOR_DOCKER', '/var/run/docker.sock')
DB_PATH = Path(os.environ.get('HOMEHUB_COLLECTOR_DB', '/data/collector/metrics.db'))
BIND_PORT = int(os.environ.get('HOMEHUB_COLLECTOR_PORT', '9100'))
INTERVAL = max(2, int(os.environ.get('HOMEHUB_COLLECTOR_INTERVAL', '5')))
PROBE_INTERVAL = max(10, int(os.environ.get('HOMEHUB_COLLECTOR_PROBE_INTERVAL', '60')))
PROBE_TIMEOUT = float(os.environ.get('HOMEHUB_COLLECTOR_PROBE_TIMEOUT', '5'))
# Public domains usually fail behind NAT hairpin; probe them against this
# internal front-door endpoint instead, keeping the original Host header.
PROBE_VIA = os.environ.get('HOMEHUB_COLLECTOR_PROBE_VIA', '').strip()
KEEP_RAW = 2 * 3600
KEEP_MINUTES = 14 * 24 * 3600
KEEP_PROBES = 3 * 24 * 3600
DOCKER_API = '/v1.44'
# Per-container counters come from the host cgroup tree (cgroup v2, systemd
# scopes) instead of the docker stats API, whose one-shot endpoint proved
# pathologically slow on this daemon.
CGROUP_BASE = Path(os.environ.get('HOMEHUB_COLLECTOR_CGROUP', '/host/cgroup'))
SECTOR = 512
MAX_TARGETS = 200
# Remote fleet: a small devices.json next to the DB describes the other boxes
# (Mac mini, OpenWrt router) which are pulled over SSH with a dedicated key.
DEVICES_FILE = Path(os.environ.get('HOMEHUB_COLLECTOR_DEVICES', '/data/collector/devices.json'))
SSH_KEY = Path(os.environ.get('HOMEHUB_COLLECTOR_SSH_KEY', '/data/collector/monitor_ed25519'))
KNOWN_HOSTS = Path(os.environ.get('HOMEHUB_COLLECTOR_KNOWN_HOSTS', '/data/collector/known_hosts'))
SSH_TIMEOUT = 20
DEVICE_KINDS = ('nas', 'mac', 'openwrt')
# Volumes to report, "container path:display name" pairs, first wins on dupes.
VOLUMES = []
for pair in os.environ.get('HOMEHUB_COLLECTOR_VOLUMES', '/host/root:/,/vol1:/vol1,/vol2:/vol2,/vol3:/vol3').split(','):
    pair = pair.strip()
    if pair and ':' in pair:
        source, label = pair.split(':', 1)
        if not any(v[1] == label for v in VOLUMES):
            VOLUMES.append((source, label))

RANGES = {'1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800, '14d': 1296000}
STEPS = {'1h': 60, '6h': 120, '24h': 300, '7d': 1800, '14d': 3600}
PRIVATE_HOST = re.compile(r'^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.|::1|localhost)', re.I)


def null_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float('inf'), float('-inf')) else None


def read_small(path, default=None):
    try:
        return path.read_text().strip()
    except OSError:
        return default


class Sampler:
    """Turns host counters into rate samples; all readers degrade to None."""

    def __init__(self, device=''):
        self.device = device
        self.prev_cpu = None
        self.prev_cores = None
        self.prev_net = {}
        self.prev_io = {}
        self.prev_docker = {}
        self.docker_at = None
        self.disk_map = {}
        self.disk_map_at = 0.0
        self.last = (None, [])
        self.lock = threading.Lock()

    # ---- /proc and sysfs readers -------------------------------------------------

    def cpu(self):
        total = cores = None
        try:
            for line in (PROC / 'stat').read_text().splitlines():
                if total is None and line.startswith('cpu '):
                    total = [float(x) for x in line.split()[1:]]
                elif line.startswith('cpu') and line[3:4].isdigit():
                    if cores is None:
                        cores = []
                    cores.append([float(x) for x in line.split()[1:]])
        except (OSError, ValueError):
            return None
        if not total:
            return None

        def used(now, before):
            if not before or now[0] - before[0] <= 0 or sum(now) - sum(before) <= 0:
                return None
            return round(100 * (1 - (now[3] + now[4] - before[3] - before[4]) / (sum(now) - sum(before))), 1)

        usage = used(total, self.prev_cpu)
        core_usage = [used(one, self.prev_cores[i] if self.prev_cores and i < len(self.prev_cores) else None)
                      for i, one in enumerate(cores or [])] if cores else None
        self.prev_cpu, self.prev_cores = total, cores
        return {'usage': usage, 'cores': core_usage, 'counters': total, 'core_counters': cores}

    @staticmethod
    def memory():
        try:
            fields = {}
            for line in (PROC / 'meminfo').read_text().splitlines():
                key, _, rest = line.partition(':')
                fields[key] = null_float(rest.split()[0])
        except (OSError, ValueError):
            return None
        total = fields.get('MemTotal')
        available = fields.get('MemAvailable')
        if not total:
            return None
        swap = fields.get('SwapTotal') or 0
        cached = (fields.get('Cached') or 0) + (fields.get('SReclaimable') or 0) + (fields.get('Buffers') or 0)
        scale = 1024
        return {'total': total * scale, 'available': (available or 0) * scale,
                'used': (total - (available or 0)) * scale, 'cached': cached * scale,
                'swap_total': swap * scale, 'swap_used': (swap - (fields.get('SwapFree') or 0)) * scale}

    @staticmethod
    def load():
        try:
            fields = (PROC / 'loadavg').read_text().split()
            running, total = fields[3].split('/')
            return {'avg': [null_float(x) for x in fields[:3]], 'running': int(running),
                    'procs': int(total), 'last_pid': int(fields[4])}
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def uptime():
        try:
            value = null_float((PROC / 'uptime').read_text().split()[0])
            return round(value) if value is not None else None
        except (OSError, IndexError):
            return None

    def network(self):
        base = SYS / 'class' / 'net'
        try:
            names = sorted(p.name for p in base.iterdir() if p.is_dir())
        except OSError:
            return None
        result = {}
        for name in names:
            counter = {}
            for key in ('rx_bytes', 'tx_bytes'):
                value = read_small(base / name / 'statistics' / key)
                counter[key] = null_float(value)
            if counter['rx_bytes'] is None:
                continue
            entry = {'rx_bytes': counter['rx_bytes'], 'tx_bytes': counter['tx_bytes'],
                     'operstate': read_small(base / name / 'operstate', 'unknown'),
                     'speed': null_float(read_small(base / name / 'speed'))}
            before = self.prev_net.get(name)
            self.prev_net[name] = counter
            if before and counter['rx_bytes'] is not None:
                entry['rx'] = round(max(0.0, (counter['rx_bytes'] - before['rx_bytes']) / INTERVAL), 0)
                entry['tx'] = round(max(0.0, (counter['tx_bytes'] - before['tx_bytes']) / INTERVAL), 0)
            else:
                entry['rx'] = entry['tx'] = None
            if name != 'lo' and (entry['rx'] or 0) + (entry['tx'] or 0) >= 0:
                result[name] = entry
        return result or None

    def disks(self):
        try:
            devices = {}
            for line in (PROC / 'diskstats').read_text().splitlines():
                parts = line.split()
                if len(parts) < 14:
                    continue
                name = parts[2]
                if not re.fullmatch(r'(sd[a-z]+|nvme\d+n\d+|vd[a-z]+|md\d+)', name):
                    continue
                devices[name] = {'read_sectors': float(parts[5]), 'write_sectors': float(parts[9]),
                                 'io_ticks': float(parts[12])}
        except (OSError, ValueError, IndexError):
            return None
        result = {}
        for name, now in devices.items():
            before = self.prev_io.get(name)
            self.prev_io[name] = now
            entry = {'reads': now['read_sectors'], 'writes': now['write_sectors']}
            if before:
                dt = max(now['io_ticks'] - before['io_ticks'], 0) / 1000 or INTERVAL
                entry['r'] = round(max(0.0, (now['read_sectors'] - before['read_sectors']) * SECTOR / dt), 0)
                entry['w'] = round(max(0.0, (now['write_sectors'] - before['write_sectors']) * SECTOR / dt), 0)
                entry['util'] = round(min(100.0, 100 * (now['io_ticks'] - before['io_ticks']) / (dt * 1000)), 1)
            else:
                entry['r'] = entry['w'] = entry['util'] = None
            result[name] = entry
        return result or None

    def refresh_disk_map(self):
        mapping = {}
        for block in (SYS / 'class' / 'block').glob('*'):
            if not re.fullmatch(r'(sd[a-z]+|nvme\d+n\d+)', block.name):
                continue
            target = Path(block.name)
            device = block / 'device'
            if device.exists():
                resolved = device.resolve()
                marker = resolved.name if re.fullmatch(r'\d+:\d+:\d+:\d+', resolved.name) else None
                if marker:
                    target = marker
            mapping[str(target)] = block.name
        self.disk_map, self.disk_map_at = mapping, time.monotonic()

    def temperatures(self):
        if time.monotonic() - self.disk_map_at > 300:
            self.refresh_disk_map()
        result = {}
        try:
            monitors = sorted((SYS / 'class' / 'hwmon').glob('hwmon*'))
        except OSError:
            return None
        core_max = None
        for hw in monitors:
            chip = read_small(hw / 'name', '')
            device = hw / 'device'
            identity = readlink_name(device)
            for source in sorted(hw.glob('temp*_input')):
                label = read_small(source.with_name(source.name.replace('_input', '_label')), '')
                try:
                    value = round(float(source.read_text().strip()) / 1000, 1)
                except (OSError, ValueError):
                    continue
                key = None
                if chip == 'coretemp':
                    if label.startswith('Package'):
                        key = 'cpu'
                    elif label.startswith('Core') and (core_max is None or value > core_max):
                        core_max = value
                elif chip == 'nvme':
                    key = identity or 'nvme'
                elif chip == 'drivetemp':
                    key = self.disk_map.get(identity or '', None)
                    if not key:
                        key = 'disk?' + (identity or '?')
                elif chip in ('acpitz', 'i915', 'pch_cannonlake', 'pch'):
                    key = {'i915': 'gpu', 'pch_cannonlake': 'pch'}.get(chip, chip)
                if key and value:
                    previous = result.get(key)
                    if not previous or value > previous:
                        result[key] = value
        if core_max is not None:
            result['cpu_cores_max'] = core_max
        return result or None

    # ---- volumes -----------------------------------------------------------------

    def volumes(self):
        result = {}
        mount_info = read_mounts()
        for source, label in VOLUMES:
            try:
                stat = os.statvfs(source)
            except OSError:
                continue
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bfree * stat.f_frsize
            if not total:
                continue
            entry = {'total': total, 'used': total - free, 'free': free}
            info = mount_info.get(source)
            if info:
                entry['device'], entry['fstype'] = info
            result[label] = entry
        return result or None

    # ---- Docker ------------------------------------------------------------------

    def docker(self):
        try:
            listing = docker_request('GET', f'{DOCKER_API}/containers/json?all=1')
            containers = json.loads(listing)
        except (OSError, ValueError, http.client.HTTPException) as error:
            return None, [], str(error)
        now = time.time()
        running = [item for item in containers if item.get('State') == 'running']
        result = []
        totals = {'running': len(running), 'total': len(containers), 'cpu': 0.0, 'mem': 0}
        online = INFO.get('cpus') or os.cpu_count() or 1
        for item in containers:
            full_id = item.get('Id') or ''
            one_id = full_id[:12]
            entry = {'id': one_id, 'name': (item.get('Names') or [''])[0].lstrip('/'),
                     'image': item.get('Image', ''), 'state': item.get('State', 'unknown'),
                     'status': item.get('Status', ''), 'created': item.get('Created')}
            project = (item.get('Labels') or {}).get('com.docker.compose.project')
            if project:
                entry['project'] = project
            status = entry['status']
            if 'healthy' in status:
                entry['health'] = 'healthy'
            elif 'unhealthy' in status:
                entry['health'] = 'unhealthy'
            elif 'starting' in status:
                entry['health'] = 'starting'
            if entry['state'] == 'running':
                usage = cgroup_usage(full_id)
                before = self.prev_docker.get(one_id)
                elapsed = self.docker_at and max(1.0, now - self.docker_at) or None
                if usage:
                    entry['mem'] = usage['mem']
                    entry['mem_limit'] = usage['limit']
                    if before and elapsed:
                        entry['cpu'] = cpu_percent(usage, before, online, elapsed)
                        for key in ('blk_r', 'blk_w'):
                            if usage[key] is not None and before[key] is not None:
                                entry[key] = round(max(0.0, (usage[key] - before[key]) / elapsed), 0)
                    totals['cpu'] += entry.get('cpu') or 0
                    totals['mem'] += entry.get('mem') or 0
                self.prev_docker[one_id] = usage
            result.append(entry)
        self.docker_at = now
        live = {item['Id'][:12] for item in running}
        for gone in [key for key in self.prev_docker if key not in live]:
            del self.prev_docker[gone]
        totals['cpu'] = round(totals['cpu'], 1)
        return totals, result, None

    def sample(self):
        now = int(time.time())
        cpu = self.cpu()
        mem = self.memory()
        net = self.network()
        io = self.disks()
        primary = None
        if net:
            candidates = {name: (one.get('rx') or 0) + (one.get('tx') or 0)
                          for name, one in net.items() if name != 'lo' and one.get('operstate') != 'down'}
            busiest = max(candidates.items(), key=lambda kv: kv[1]) if candidates else None
            if busiest and busiest[1] > 0:
                primary = busiest[0]
        totals, containers, docker_error = self.docker()
        sample = {'t': now,
                  'cpu': cpu['usage'] if cpu else None,
                  'cores': cpu['cores'] if cpu else None,
                  'mem': mem, 'load': self.load(), 'uptime': self.uptime(),
                  'net': net, 'net_primary': primary, 'io': io,
                  'vol': self.volumes(), 'temp': self.temperatures(),
                  'docker': totals,
                  'docker_error': docker_error}
        with self.lock:
            STORE.push(sample, self.device)
            STORE.persist_containers(now, containers)
            self.last = (sample, containers)
        return sample, containers


def readlink_name(path):
    try:
        return os.path.basename(os.path.realpath(path))
    except OSError:
        return None


def read_mounts():
    """Map container mount point -> (source device, filesystem type)."""
    result = {}
    try:
        lines = (PROC / 'self' / 'mountinfo').read_text().splitlines()
    except OSError:
        return result
    for line in lines:
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            sep = parts.index('-')
        except ValueError:
            continue
        if sep + 2 < len(parts):
            result[urllib.parse.unquote(parts[4])] = (urllib.parse.unquote(parts[sep + 2]), parts[sep + 1])
    return result


# ---- Docker socket client --------------------------------------------------------

class DockerConnection(http.client.HTTPConnection):
    def __init__(self, timeout=8):
        super().__init__('localhost', timeout=timeout)
        self.sock_path = DOCKER_SOCK

    def connect(self):
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(self.timeout)
        raw.connect(self.sock_path)
        self.sock = raw


def docker_request(method, path, timeout=8):
    connection = DockerConnection(timeout)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.read()
    finally:
        connection.close()


def cgroup_usage(container_id):
    """Read one container's counters straight from its systemd cgroup scope."""
    scope = CGROUP_BASE / 'system.slice' / f'docker-{container_id}.scope'
    try:
        cpu_text = (scope / 'cpu.stat').read_text()
        mem_current = int((scope / 'memory.current').read_text().strip())
    except (OSError, ValueError):
        return None
    usage_usec = None
    for line in cpu_text.splitlines():
        if line.startswith('usage_usec'):
            usage_usec = null_float(line.split()[1])
            break
    if usage_usec is None:
        return None
    mem_stat_text = ''
    try:
        mem_stat_text = (scope / 'memory.stat').read_text()
    except OSError:
        pass
    inactive = 0
    for line in mem_stat_text.splitlines():
        if line.startswith('inactive_file '):
            inactive = int(line.split()[1])
            break
    try:
        mem_max = (scope / 'memory.max').read_text().strip()
    except OSError:
        mem_max = 'max'
    read_bytes = write_bytes = 0
    try:
        for line in (scope / 'io.stat').read_text().splitlines():
            for field in line.split()[1:]:
                if field.startswith('rbytes='):
                    read_bytes += int(field.split('=')[1])
                elif field.startswith('wbytes='):
                    write_bytes += int(field.split('=')[1])
    except (OSError, ValueError, IndexError):
        pass
    return {'cpu_usec': usage_usec, 'mem': mem_current - inactive, 'mem_current': mem_current,
            'limit': None if mem_max == 'max' else null_float(mem_max),
            'blk_r': read_bytes, 'blk_w': write_bytes}


def cpu_percent(now, before, online, elapsed):
    if not now or not before or elapsed <= 0:
        return None
    delta = now['cpu_usec'] - before['cpu_usec']
    if delta <= 0:
        return None
    # usage_usec counts every core; 100% of the box = elapsed * 1e6 * online.
    capacity = elapsed * 1e6 * online
    return round(min(100.0 * online, 100 * delta / capacity), 2)


# ---- SQLite store ----------------------------------------------------------------

def connect(path=None):
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA synchronous=NORMAL')
    return connection


class Store:
    def __init__(self, path):
        self.path = path
        self.raw = {}
        self.rollups = {}
        self.last_prune = 0
        self.init_schema()

    def ring(self, device):
        if device not in self.raw:
            self.raw[device] = deque(maxlen=KEEP_RAW // INTERVAL + 8)
        return self.raw[device]

    def init_schema(self):
        with connect(self.path) as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS samples(ts INTEGER, device TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL, PRIMARY KEY(ts, device));
            CREATE TABLE IF NOT EXISTS containers(ts INTEGER NOT NULL, name TEXT NOT NULL,
                state TEXT NOT NULL, cpu REAL, mem INTEGER, PRIMARY KEY(ts, name));
            CREATE TABLE IF NOT EXISTS probes(ts INTEGER NOT NULL, key TEXT NOT NULL,
                ok INTEGER NOT NULL, ms INTEGER, code INTEGER, PRIMARY KEY(ts, key));
            CREATE TABLE IF NOT EXISTS targets(key TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_containers_ts ON containers(ts);
            CREATE INDEX IF NOT EXISTS idx_probes_ts ON probes(ts);
            ''')

    def push(self, sample, device):
        ring = self.ring(device)
        ring.append(sample)
        minute = sample['t'] // 60 * 60
        last = self.rollups.get(device, 0)
        if minute > last:
            self.rollup(device, minute - 60)

    def rollup(self, device, minute):
        """Persist the newest raw sample of the finished minute for one device."""
        if minute <= 0:
            self.rollups[device] = minute + 60
            return
        chosen = None
        for sample in self.ring(device):
            if sample['t'] // 60 * 60 == minute:
                chosen = sample
        if chosen is not None:
            with connect(self.path) as c:
                c.execute('INSERT OR REPLACE INTO samples(ts,device,data) VALUES(?,?,?)',
                          (minute, device, json.dumps(chosen, ensure_ascii=False, separators=(',', ':'))))
        self.rollups[device] = minute
        if time.monotonic() - self.last_prune > 1800:
            self.prune()

    def persist_containers(self, now, containers):
        minute = now // 60 * 60
        rows = [(minute, one['name'], one['state'], one.get('cpu'), one.get('mem'))
                for one in containers if one.get('name')]
        with connect(self.path) as c:
            c.executemany('INSERT OR REPLACE INTO containers(ts,name,state,cpu,mem) VALUES(?,?,?,?,?)', rows)

    def prune(self):
        self.last_prune = time.monotonic()
        now = int(time.time())
        with connect(self.path) as c:
            c.execute('DELETE FROM samples WHERE ts < ?', (now - KEEP_MINUTES,))
            c.execute("DELETE FROM samples WHERE device=''")

            c.execute('DELETE FROM containers WHERE ts < ?', (now - KEEP_MINUTES,))
            c.execute('DELETE FROM probes WHERE ts < ?', (now - KEEP_PROBES,))

    def tail(self, count=90, device=''):
        return list(self.ring(device))[-count:]

    def history(self, range_key, device='', extras=True):
        span = RANGES[range_key]
        step = STEPS[range_key]
        now = int(time.time())
        floor = (now - span) // 60 * 60
        system = {'t': [], 'cpu': [], 'cores': [], 'load': [], 'mem_used': [], 'mem_cached': [],
                  'swap_used': [], 'net': {}, 'io': {}, 'vol': {}, 'temp': {}, 'docker_cpu': [],
                  'docker_mem': [], 'docker_running': [], 'uptime': [], 'procs': []}
        latest = self.tail(1, device)
        with connect(self.path) as c:
            rows = c.execute('SELECT ts,data FROM samples WHERE ts>=? AND device=? ORDER BY ts',
                             (floor, device)).fetchall()
        stream = [json.loads(one[1]) for one in rows if (one[0] // 60 * 60) % step < 60 or one[0] == floor]
        for sample in stream:
            minute = sample['t'] // 60 * 60
            if system['t'] and minute <= system['t'][-1]:
                continue
            append_series(system, sample)
        if latest and (not system['t'] or latest[0]['t'] - system['t'][-1] >= 60):
            append_series(system, latest[0])
        pad_series(system)
        containers = self.container_summary(floor) if extras else []
        probes = self.probe_summary(floor) if extras else []
        return {'range': range_key, 'step': step, 'from': floor, 'to': now, 'device': device,
                'system': system, 'containers': containers, 'probes': probes}

    def container_summary(self, floor):
        with connect(self.path) as c:
            rows = c.execute('SELECT name,AVG(cpu),MAX(cpu),AVG(mem),MAX(mem),COUNT(*) FROM containers '
                             'WHERE ts>=? AND state=\'running\' GROUP BY name', (floor,)).fetchall()
        return [{'name': one[0], 'avg_cpu': round(one[1], 2) if one[1] is not None else None,
                 'max_cpu': round(one[2], 2) if one[2] is not None else None,
                 'avg_mem': round(one[3]) if one[3] is not None else None,
                 'max_mem': round(one[4]) if one[4] is not None else None,
                 'samples': one[5]} for one in rows]

    def container_history(self, name, floor, step):
        with connect(self.path) as c:
            rows = c.execute('SELECT ts,cpu,mem FROM containers WHERE name=? AND ts>=? ORDER BY ts',
                             (name, floor)).fetchall()
        result = {'name': name, 't': [], 'cpu': [], 'mem': []}
        for ts, cpu, mem in rows:
            if ts % step >= 60:
                continue
            if result['t'] and ts <= result['t'][-1]:
                continue
            result['t'].append(ts)
            result['cpu'].append(cpu)
            result['mem'].append(mem)
        summary = next((one for one in self.container_summary(floor) if one['name'] == name), None)
        result['summary'] = summary
        return result

    def probe_summary(self, floor):
        with connect(self.path) as c:
            targets = c.execute('SELECT key,name,url FROM targets').fetchall()
            stats = {one[0]: one for one in c.execute(
                'SELECT key,AVG(ok),COUNT(*),AVG(CASE WHEN ok=1 THEN ms END),MAX(ts) '
                'FROM probes WHERE ts>=? GROUP BY key', (floor,))}
            latest = {one[0]: one for one in c.execute(
                'SELECT key,ts,ok,ms,code FROM probes WHERE ts IN (SELECT MAX(ts) FROM probes GROUP BY key)')}
        result = []
        for key, name, url in targets:
            stat = stats.get(key)
            host = urllib.parse.urlsplit(url).netloc or url
            entry = {'key': key, 'name': name, 'host': host,
                     'rate': round(stat[1], 4) if stat and stat[2] else None,
                     'checks': stat[2] if stat else 0,
                     'avg_ms': round(stat[3]) if stat and stat[3] is not None else None,
                     'last': None}
            last = latest.get(key)
            if last:
                entry['last'] = {'ts': last[1], 'ok': bool(last[2]), 'ms': last[3], 'code': last[4]}
            result.append(entry)
        return result


def pad_series(system):
    """Keep every series the same length as the time axis, nulls for gaps."""
    size = len(system['t'])

    def pad(values):
        if len(values) < size:
            values.extend([None] * (size - len(values)))
        return values[:size]

    for key, value in list(system.items()):
        if key == 't' or value is None:
            continue
        if isinstance(value, list) and value and isinstance(value[0], list):
            for one in value:
                pad(one)
        elif isinstance(value, list):
            pad(value)
        elif isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, dict):
                    for series in nested.values():
                        pad(series)
                elif isinstance(nested, list):
                    pad(nested)


def append_series(system, sample):
    system['t'].append(sample['t'])
    system['cpu'].append(sample.get('cpu'))
    cores = sample.get('cores')
    if cores is not None:
        while len(system['cores']) < len(cores):
            system['cores'].append([])
        for i, value in enumerate(cores):
            system['cores'][i].append(value)
    else:
        for series in system['cores']:
            series.append(None)
    load = sample.get('load')
    system['load'].append(load['avg'][0] if load else None)
    mem = sample.get('mem')
    system['mem_used'].append(mem.get('used') if mem else None)
    system['mem_cached'].append(mem.get('cached') if mem else None)
    system['swap_used'].append(mem.get('swap_used') if mem else None)
    net = sample.get('net') or {}
    for name, one in net.items():
        series = system['net'].setdefault(name, {'rx': [], 'tx': []})
        series['rx'].append(one.get('rx'))
        series['tx'].append(one.get('tx'))
    io = sample.get('io') or {}
    for name, one in io.items():
        series = system['io'].setdefault(name, {'r': [], 'w': [], 'util': []})
        series['r'].append(one.get('r'))
        series['w'].append(one.get('w'))
        series['util'].append(one.get('util'))
    vol = sample.get('vol') or {}
    for name, one in vol.items():
        series = system['vol'].setdefault(name, {'used': [], 'total': []})
        series['used'].append(one.get('used'))
        series['total'].append(one.get('total'))
    temp = sample.get('temp') or {}
    for name, value in temp.items():
        system['temp'].setdefault(name, []).append(value)
    for name in system['temp']:
        if name not in temp:
            system['temp'][name].append(None)
    docker = sample.get('docker') or {}
    system['docker_cpu'].append(docker.get('cpu'))
    system['docker_mem'].append(docker.get('mem'))
    system['docker_running'].append(docker.get('running'))
    system['uptime'].append(sample.get('uptime'))
    system['procs'].append(sample.get('load', {}).get('procs') if sample.get('load') else None)


# ---- Probes ----------------------------------------------------------------------

class Prober:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()

    def set_targets(self, targets):
        rows = []
        seen = set()
        for one in targets[:MAX_TARGETS]:
            if not isinstance(one, dict):
                continue
            name = str(one.get('name', ''))[:80]
            url = str(one.get('url', ''))[:2048]
            parts = urllib.parse.urlsplit(url)
            if parts.scheme not in ('http', 'https') or not parts.hostname:
                continue
            key = one.get('key')
            key = str(key)[:64] if key else 'u' + hashlib.sha256(url.encode()).hexdigest()[:12]
            if key in seen:
                continue
            seen.add(key)
            rows.append((key, name or parts.hostname, url))
        with connect(self.store.path) as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM targets')
            c.executemany('INSERT INTO targets(key,name,url) VALUES(?,?,?)', rows)
        return len(rows)

    def list_targets(self):
        with connect(self.store.path) as c:
            return c.execute('SELECT key,name,url FROM targets ORDER BY key').fetchall()

    @staticmethod
    def probe_once(url):
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ''
        origin = urllib.parse.urlsplit(PROBE_VIA) if PROBE_VIA else None
        via = bool(origin and origin.hostname) and not PRIVATE_HOST.match(host)
        if via:
            target_host, target_port, prefix = origin.hostname, origin.port, origin.scheme
        else:
            target_host, _, port_text = parts.netloc.partition(':')
            target_port = int(port_text) if port_text.isdigit() else None
            prefix = parts.scheme
        path = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
        headers = {}
        if via:
            headers['Host'] = parts.netloc
        started = time.monotonic()
        code = None
        try:
            if prefix == 'https':
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                connection = http.client.HTTPSConnection(target_host, port=target_port, timeout=PROBE_TIMEOUT, context=context)
            else:
                connection = http.client.HTTPConnection(target_host, port=target_port, timeout=PROBE_TIMEOUT)
            try:
                connection.request('GET', path, headers=headers)
                response = connection.getresponse()
                code = response.status
                response.read(512)
            finally:
                connection.close()
            ok = code < 500
        except (OSError, http.client.HTTPException, ValueError):
            ok, code = False, None
        return {'ok': ok, 'code': code, 'ms': round((time.monotonic() - started) * 1000)}

    def run_forever(self):
        while True:
            started = time.monotonic()
            try:
                self.probe_round()
            except Exception as error:
                print('Probe round failed:', type(error).__name__, flush=True)
            rest = PROBE_INTERVAL - (time.monotonic() - started)
            if rest > 0:
                time.sleep(rest)

    def probe_round(self):
        """Probe every target with a small worker pool so one round stays well
        under PROBE_INTERVAL even when many endpoints are slow or timing out."""
        targets = self.list_targets()
        now = int(time.time())
        queue = list(targets)
        lock = threading.Lock()

        def worker():
            while True:
                with lock:
                    if not queue:
                        return
                    key, _name, url = queue.pop()
                try:
                    result = self.probe_once(url)
                except Exception:
                    result = {'ok': False, 'code': None, 'ms': None}
                with connect(self.store.path) as c:
                    c.execute('INSERT OR REPLACE INTO probes(ts,key,ok,ms,code) VALUES(?,?,?,?,?)',
                              (now, key, 1 if result['ok'] else 0, result['ms'], result['code']))

        workers = [threading.Thread(target=worker, daemon=True) for _ in range(6)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(PROBE_INTERVAL)


# ---- Remote devices over SSH -----------------------------------------------------

MAC_COMMAND = ("uptime; echo '---TOP'; top -l 2 -n 0 | grep -E '^(CPU usage|PhysMem|Processes)'; "
               "echo '---DF'; df -k /; echo '---NET'; netstat -ib; "
               "echo '---ID'; hostname; sw_vers -productVersion; uname -r; uname -m")
OPENWRT_COMMAND = ("echo '---STAT'; cat /proc/stat; echo '---LOAD'; cat /proc/loadavg; "
                   "echo '---MEM'; cat /proc/meminfo; echo '---NET'; cat /proc/net/dev; "
                   "echo '---UP'; cat /proc/uptime; echo '---TEMP'; "
                   "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null; echo '---CONN'; "
                   "cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null; "
                   "cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null; echo '---DHCP'; "
                   "wc -l < /tmp/dhcp.leases 2>/dev/null; echo '---LEASES'; cat /tmp/dhcp.leases 2>/dev/null; "
                   "echo '---NEIGH'; ip neigh 2>/dev/null; echo '---WAN'; "
                   "ubus call network.interface.wan status 2>/dev/null; echo '---WIFI'; "
                   "for i in $(iwinfo 2>/dev/null | awk '{print $1}'); do "
                   "iwinfo $i assoclist 2>/dev/null; done; echo '---FIREWALL'; "
                   "uci show firewall 2>/dev/null; echo '---BOARD'; ubus call system board 2>/dev/null")
DEVICE_COMMANDS = {'mac': MAC_COMMAND, 'openwrt': OPENWRT_COMMAND}


def ssh_run(target, kind, timeout=SSH_TIMEOUT):
    """One read-only command batch over the dedicated monitor key."""
    if not SSH_KEY.is_file():
        raise RuntimeError('monitor SSH key not present')
    argv = ['ssh', '-i', str(SSH_KEY), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + str(KNOWN_HOSTS), '-o', 'ConnectTimeout=6',
            '-o', 'PreferredAuthentications=publickey', target, DEVICE_COMMANDS[kind]]
    result = subprocess.run(argv, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.decode('utf-8', 'replace').strip().splitlines()
        raise RuntimeError(detail[-1][:120] if detail else f'ssh exited {result.returncode}')
    return result.stdout.decode('utf-8', 'replace')


def human_bytes(text_value):
    """'12G' / '3874M' / '512K' style macOS sizes into bytes."""
    if not text_value:
        return None
    multiplier = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4, 'P': 1024 ** 5}
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([KMGT]?)', str(text_value).strip())
    if not match:
        return None
    value = float(match.group(1))
    return int(value * multiplier.get(match.group(2), 1))


def split_markers(text):
    parts, current = {}, None
    for line in text.splitlines():
        match = re.fullmatch(r'---(\w+)', line.strip())
        if match:
            current = match.group(1).lower()
            parts[current] = []
        elif current:
            parts[current].append(line)
    return {key: [one for one in value if one.strip()] for key, value in parts.items()}


def parse_mac_report(text):
    """Read the macOS one-shot report; CPU comes from the second top sample."""
    sections = split_markers(text)
    sample = {'cpu': None, 'cores': None, 'mem': None, 'load': None, 'uptime': None,
              'net': None, 'net_primary': None, 'io': None, 'vol': None, 'temp': None,
              'docker': None, 'docker_error': None, 'processes': None}
    for line in sections.get('top', []):
        if line.startswith('CPU usage:'):
            numbers = {key: null_float(value) for value, key in
                       re.findall(r'(\d+(?:\.\d+)?)%\s*(user|sys|idle)', line)}
            idle = numbers.get('idle')
            if idle is not None:
                sample['cpu'] = round(max(0.0, min(100.0, 100 - idle)), 1)
        elif line.startswith('PhysMem:'):
            used = human_bytes(re.search(r'([\d.]+[KMGT])\s*used', line).group(1)) if re.search(r'([\d.]+[KMGT])\s*used', line) else None
            unused = human_bytes(re.search(r'([\d.]+[KMGT])\s*unused', line).group(1)) if re.search(r'([\d.]+[KMGT])\s*unused', line) else None
            if used is not None and unused is not None:
                sample['mem'] = {'total': used + unused, 'used': used, 'available': unused,
                                 'cached': None, 'swap_total': 0, 'swap_used': None}
        elif line.startswith('Processes:'):
            match = re.search(r'(\d+)\s*total', line)
            if match:
                sample['processes'] = int(match.group(1))
    up_line = next((one for one in sections.get('', []) if ' up ' in one or one.strip().startswith('up ')), '')
    up_text = next((one for one in text.splitlines() if re.search(r'\bup\b', one) and 'load' in one.lower()), up_line)
    load_match = re.search(r'load averages?:\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)', up_text)
    if load_match:
        sample['load'] = {'avg': [float(load_match.group(i)) for i in (1, 2, 3)],
                          'running': None, 'procs': sample['processes'], 'last_pid': None}
    if up_text:
        sample['uptime'] = parse_uptime_text(up_text)
    for line in sections.get('df', [])[1:]:
        fields = line.split()
        if len(fields) >= 9 and fields[8] == '/':
            total, used, available = (int(fields[1]) * 1024, int(fields[2]) * 1024, int(fields[3]) * 1024)
            sample['vol'] = {'/': {'total': total, 'used': used, 'free': available, 'fstype': 'apfs'}}
    counters = {}
    for line in sections.get('net', []):
        fields = line.split()
        if len(fields) >= 10 and re.match(r'^en\d+$', fields[0]) and fields[2].startswith('<Link#'):
            try:
                counters[fields[0]] = {'rx_bytes': float(fields[6]), 'tx_bytes': float(fields[9])}
            except ValueError:
                continue
    sample['_net_counters'] = counters or None
    ident = [one.strip() for one in sections.get('id', [])]
    if len(ident) >= 4:
        sample['_identity'] = {'hostname': ident[0], 'os': 'macOS ' + ident[1],
                               'kernel': ident[2], 'arch': ident[3]}
    return sample


def parse_uptime_text(text):
    match = re.search(r'\bup\s+(.+?)(?:,\s*\d+\s*users?|,\s*load|$)', text)
    if not match:
        return None
    rest = match.group(1).strip()
    total = 0
    days = re.search(r'(\d+)\s*days?', rest)
    if days:
        total += int(days.group(1)) * 86400
        rest = rest[:days.start()] + rest[days.end():]
    clock = re.search(r'(\d+):(\d+)(?::(\d+))?', rest)
    if clock:
        parts = [int(x) for x in clock.groups('0')]
        if len(parts) == 3:
            total += parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            total += parts[0] * 3600 + parts[1] * 60
    else:
        minutes = re.search(r'(\d+)\s*mins?', rest)
        if minutes:
            total += int(minutes.group(1)) * 60
    return total or None


def parse_openwrt_report(text):
    sections = split_markers(text)
    sample = {'cpu': None, 'cores': None, 'mem': None, 'load': None, 'uptime': None,
              'net': None, 'net_primary': None, 'io': None, 'vol': None, 'temp': None,
              'docker': None, 'docker_error': None, 'conntrack': None, 'dhcp_leases': None,
              'firmware': None, 'model': None}
    stat_lines = sections.get('stat', [])
    total = cores = None
    if stat_lines and stat_lines[0].startswith('cpu '):
        total = [float(x) for x in stat_lines[0].split()[1:]]
        cores = [[float(x) for x in line.split()[1:]] for line in stat_lines[1:] if line.startswith('cpu')]
    sample['_cpu_counters'] = (total, cores)
    load = next((one for one in sections.get('load', [])), '')
    if load:
        fields = load.split()
        running, total_procs = fields[3].split('/')
        sample['load'] = {'avg': [null_float(x) for x in fields[:3]], 'running': int(running),
                          'procs': int(total_procs), 'last_pid': None}
    mem = {}
    for line in sections.get('mem', []):
        key, _, rest = line.partition(':')
        mem[key] = null_float(rest.split()[0]) if rest.split() else None
    if mem.get('MemTotal'):
        scale = 1024
        available = mem.get('MemAvailable') or mem.get('MemFree') or 0
        sample['mem'] = {'total': mem['MemTotal'] * scale, 'available': available * scale,
                         'used': (mem['MemTotal'] - available) * scale, 'cached': None,
                         'swap_total': (mem.get('SwapTotal') or 0) * scale,
                         'swap_used': ((mem.get('SwapTotal') or 0) - (mem.get('SwapFree') or 0)) * scale}
    up = next((one for one in sections.get('up', [])), '')
    if up:
        try:
            sample['uptime'] = round(float(up.split()[0]))
        except (ValueError, IndexError):
            pass
    temp = None
    for line in sections.get('temp', []):
        try:
            value = round(float(line.strip()) / 1000, 1)
        except ValueError:
            continue
        temp = value if temp is None else max(temp, value)
    if temp is not None:
        sample['temp'] = {'soc': temp}
    conn = [one for one in sections.get('conn', []) if one.strip()]
    if len(conn) >= 2:
        try:
            sample['conntrack'] = {'count': int(conn[0]), 'max': int(conn[1])}
        except ValueError:
            pass
    dhcp = next((one for one in sections.get('dhcp', []) if one.strip().isdigit()), None)
    if dhcp is not None:
        sample['dhcp_leases'] = int(dhcp)
    board_text = '\n'.join(sections.get('board', []))
    if board_text.startswith('{'):
        try:
            board = json.loads(board_text)
            release = board.get('release') or {}
            sample['firmware'] = release.get('description') or release.get('distribution')
            sample['model'] = board.get('model')
            sample['_identity'] = {'hostname': board.get('hostname'), 'os': sample['firmware'],
                                   'kernel': board.get('kernel'), 'arch': release.get('target')}
        except ValueError:
            pass
    sample['wan'] = None
    wan_text = '\n'.join(sections.get('wan', []))
    if wan_text.startswith('{'):
        try:
            wan = json.loads(wan_text)
            addresses = wan.get('ipv4-address') or []
            sample['wan'] = {'up': bool(wan.get('up')), 'proto': wan.get('proto'),
                             'uptime': wan.get('uptime'), 'ip': addresses[0]['address'] if addresses else None}
        except (ValueError, KeyError, TypeError):
            pass
    hosts = {}
    now = int(time.time())
    for line in sections.get('leases', []):
        fields = line.split()
        if len(fields) >= 4:
            try:
                if int(fields[0]) < now:
                    continue
            except ValueError:
                pass
            name = fields[3]
            hosts[fields[2]] = {'ip': fields[2], 'mac': fields[1].lower(),
                                'name': name if name != '*' else '', 'source': 'dhcp'}
    for line in sections.get('neigh', []):
        fields = line.split()
        match = re.match(r'^([\d.]+|[0-9a-f:]+)$', fields[0] if fields else '')
        if not match:
            continue
        mac = next((one.lower() for one in fields if re.fullmatch(r'[0-9a-f]{2}(?::[0-9a-f]{2}){5}', one)), None)
        state = fields[-1]
        if fields[0] in hosts:
            if mac:
                hosts[fields[0]]['mac'] = hosts[fields[0]]['mac'] or mac
        elif mac and state not in ('FAILED', 'INCOMPLETE'):
            hosts[fields[0]] = {'ip': fields[0], 'mac': mac, 'name': '', 'source': 'arp'}
    for line in sections.get('wifi', []):
        mac_match = re.search(r'([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})', line)
        signal_match = re.search(r'(-\d+)\s*dBm', line)
        if mac_match:
            mac = mac_match.group(1).lower()
            found = next((one for one in hosts.values() if one['mac'] == mac), None)
            if found:
                found['source'] = 'wifi'
                found['signal'] = int(signal_match.group(1)) if signal_match else None
            else:
                hosts[mac] = {'ip': '', 'mac': mac, 'name': '', 'source': 'wifi',
                              'signal': int(signal_match.group(1)) if signal_match else None}

    def host_sort_key(one):
        parts = one['ip'].split('.') if one['ip'] else []
        return [int(x) for x in parts] if len(parts) == 4 and all(x.isdigit() for x in parts) else [999]

    sample['lan_hosts'] = sorted(hosts.values(), key=host_sort_key)
    forwards = []
    redirect = {}
    for line in sections.get('firewall', []):
        match = re.match(r"firewall\.@redirect\[(\d+)\]\.(\w+)=(.*)", line.strip())
        if not match:
            continue
        index, key, raw_value = match.group(1), match.group(2), match.group(3).strip("'")
        redirect.setdefault(index, {})[key] = raw_value
    for index in sorted(redirect, key=lambda x: int(x)):
        entry = redirect[index]
        if entry.get('target') != 'DNAT' or not entry.get('dest_ip'):
            continue
        forwards.append({'name': entry.get('name', ''), 'proto': entry.get('proto', 'tcp udp'),
                         'src_port': entry.get('src_dport', ''), 'dest_ip': entry['dest_ip'],
                         'dest_port': entry.get('dest_port', entry.get('src_dport', ''))})
    sample['port_forwards'] = forwards
    counters = {}
    for line in sections.get('net', [])[2:]:
        fields = line.split()
        if len(fields) >= 10 and fields[0].endswith(':'):
            name = fields[0].rstrip(':')
            if name in ('lo',) or name.startswith('veth') or name.startswith('docker'):
                continue
            try:
                counters[name] = {'rx_bytes': float(fields[1]), 'tx_bytes': float(fields[9])}
            except (ValueError, IndexError):
                continue
    sample['_net_counters'] = counters or None
    return sample


class SshSampler:
    """Polls one remote box over SSH and turns its report into a sample."""

    def __init__(self, device, target, kind, interval):
        self.device = device
        self.target = target
        self.kind = kind
        self.interval = max(10, int(interval))
        self.last = (None, [])
        self.error = '尚未采集'
        self.last_seen = None
        self.info = {'hostname': device, 'kind': kind}
        self.prev_cpu = None
        self.prev_cores = None
        self.prev_net = {}
        self.lock = threading.Lock()

    def sample(self):
        try:
            raw = ssh_run(self.target, self.kind)
            parsed = (parse_mac_report if self.kind == 'mac' else parse_openwrt_report)(raw)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            with self.lock:
                self.error = str(error)[:160]
            return None
        now = int(time.time())
        sample = {'t': now}
        counters = parsed.pop('_net_counters', None)
        identity = parsed.pop('_identity', None)
        cpu_counters = parsed.pop('_cpu_counters', (None, None))
        total, cores = cpu_counters

        def used_fraction(current, before):
            if not current or not before or len(current) < 5 or len(before) < 5:
                return None
            delta_total = sum(current) - sum(before)
            delta_idle = (current[3] + current[4]) - (before[3] + before[4])
            if delta_total <= 0:
                return None
            return round(max(0.0, min(100.0, 100 * (1 - delta_idle / delta_total))), 1)

        if total is not None:
            # OpenWrt-style: percentages come from counter deltas between rounds.
            parsed.pop('cpu', None)
            parsed.pop('cores', None)
            sample['cpu'] = used_fraction(total, self.prev_cpu)
            if cores:
                series = []
                for i, one in enumerate(cores):
                    series.append(used_fraction(one, self.prev_cores[i] if self.prev_cores and i < len(self.prev_cores) else None))
                sample['cores'] = series
        else:
            # macOS-style: the report already carries the live values.
            sample['cpu'] = parsed.pop('cpu', None)
            sample['cores'] = parsed.pop('cores', None)
        self.prev_cpu, self.prev_cores = total, cores
        net = None
        if counters:
            net = {}
            for name, one in counters.items():
                entry = {'rx_bytes': one['rx_bytes'], 'tx_bytes': one['tx_bytes'],
                         'operstate': 'unknown', 'speed': None, 'rx': None, 'tx': None}
                before = self.prev_net.get(name)
                if before:
                    entry['rx'] = round(max(0.0, (one['rx_bytes'] - before['rx_bytes']) / self.interval), 0)
                    entry['tx'] = round(max(0.0, (one['tx_bytes'] - before['tx_bytes']) / self.interval), 0)
                self.prev_net[name] = one
                net[name] = entry
            busiest = max(((name, (one.get('rx') or 0) + (one.get('tx') or 0)) for name, one in net.items()),
                          key=lambda item: item[1], default=None)
            sample['net_primary'] = busiest[0] if busiest and busiest[1] > 0 else (next(iter(net), None))
        sample['net'] = net
        # The parsed template carries placeholder values; the sampler's own
        # delta-based cpu/cores/net readings are authoritative when present.
        if total is not None:
            parsed.pop('cpu', None)
            parsed.pop('cores', None)
        parsed.pop('net', None)
        parsed.pop('net_primary', None)
        sample.update({key: value for key, value in parsed.items()})
        if identity:
            self.info = {**identity, 'kind': self.kind}
        with self.lock:
            STORE.push(sample, self.device)
            self.last = (sample, [])
            self.error = None
            self.last_seen = now
        return sample


def load_devices_config():
    try:
        config = json.loads(DEVICES_FILE.read_text())
    except (OSError, ValueError):
        return []
    devices = []
    for one in config.get('devices', []) if isinstance(config, dict) else []:
        if not isinstance(one, dict):
            continue
        name = str(one.get('name', ''))[:40].strip()
        kind = one.get('kind', 'nas')
        if not name or kind not in DEVICE_KINDS:
            continue
        devices.append({'name': name, 'kind': kind, 'host': str(one.get('host', ''))[:120],
                        'role': str(one.get('role', ''))[:60], 'interval': one.get('interval', 15)})
    return devices


class DeviceManager:
    """Registry of the local sampler plus every SSH-polled device."""

    def __init__(self, local_name, local_sampler=None):
        self.entries = {}
        self.local_name = local_name
        self.local_sampler = local_sampler

    def reload(self):
        for config in load_devices_config():
            if config['kind'] == 'nas' or not config['host']:
                if config['kind'] == 'nas':
                    self.local_name = config['name']
                continue
            entry = self.entries.get(config['name'])
            if entry and entry['sampler'].kind == config['kind'] and entry['config']['host'] == config['host']:
                entry['sampler'].interval = max(10, int(config['interval']))
                entry['config'] = config
                continue
            if entry:
                continue
            sampler = SshSampler(config['name'], config['host'], config['kind'], config['interval'])
            self.entries[config['name']] = {'config': config, 'sampler': sampler}
        return self.local_name

    def meta(self):
        now = int(time.time())
        result = []
        for name in [self.local_name] + [key for key in self.entries if key != self.local_name]:
            if name in self.entries:
                entry = self.entries[name]
                sampler = entry['sampler']
                result.append({'name': name, 'kind': sampler.kind, 'role': entry['config']['role'],
                               'interval': sampler.interval, 'lastSeen': sampler.last_seen,
                               'error': sampler.error, 'online': bool(sampler.last_seen and now - sampler.last_seen < sampler.interval * 3 + 10)})
            else:
                sampler = self.local_sampler
                last_seen = now if sampler and sampler.last[0] else None
                result.append({'name': name, 'kind': 'nas', 'role': 'NAS · 家庭数据中枢',
                               'interval': INTERVAL, 'lastSeen': last_seen,
                               'error': None, 'online': bool(last_seen)})
        return result

    def get(self, name):
        if name == self.local_name or not name:
            return {'name': self.local_name, 'kind': 'nas', 'role': 'NAS · 家庭数据中枢',
                    'sampler': self.local_sampler, 'interval': INTERVAL}
        entry = self.entries.get(name)
        return {'name': name, **entry['config'], 'sampler': entry['sampler'],
                'interval': entry['sampler'].interval} if entry else None


LOCAL_SAMPLER = None
DEVICES = None


def device_loop(sampler):
    while True:
        started = time.monotonic()
        try:
            sampler.sample()
        except Exception as error:
            print('Device loop failed:', sampler.device, type(error).__name__, flush=True)
        rest = sampler.interval - (time.monotonic() - started)
        if rest > 0:
            time.sleep(rest)


# ---- Static info and HTTP surface ------------------------------------------------

def static_info():
    info = {'hostname': socket.gethostname(), 'version': VERSION}
    try:
        info['hostname'] = (ROOTFS / 'etc' / 'hostname').read_text().strip() or info['hostname']
    except OSError:
        pass
    release = read_small(ROOTFS / 'etc' / 'os-release', '')
    for line in release.splitlines():
        if line.startswith('PRETTY_NAME='):
            info['os'] = line.split('=', 1)[1].strip('"')
    info['kernel'] = read_small(PROC / 'sys' / 'kernel' / 'osrelease', os.uname().release)
    try:
        model = None
        for line in (PROC / 'cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                model = line.split(':', 1)[1].strip()
                break
        if model:
            info['cpu_model'] = model
    except OSError:
        pass
    try:
        docker_info = json.loads(docker_request('GET', f'{DOCKER_API}/info', timeout=10))
        info['docker_version'] = docker_info.get('ServerVersion')
        info['docker_root'] = docker_info.get('DockerRootDir')
        info['cpus'] = docker_info.get('NCPU')
        info['mem_total'] = docker_info.get('MemTotal')
        info['containers_total'] = docker_info.get('Containers')
        info['containers_running'] = docker_info.get('ContainersRunning')
    except (OSError, ValueError, http.client.HTTPException):
        pass
    return info


class Handler(BaseHTTPRequestHandler):
    server_version = 'HomehubCollector'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_HEAD(self):
        self.handle_request()

    def do_GET(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def handle_request(self):
        try:
            self.connection.settimeout(20)
            path = urllib.parse.urlsplit(self.path).path
            if path == '/health':
                return self.reply(200, {'ok': True, 'version': VERSION, 'uptime': SAMPLER.uptime()})
            if path == '/snapshot':
                query = urllib.parse.urlsplit(self.path).query
                params = urllib.parse.parse_qs(query)
                name = (params.get('device', [''])[0] or '')[:40]
                device = DEVICES.get(name) if name else DEVICES.get(DEVICES.local_name)
                if device is None:
                    return self.reply(404, {'error': '设备不存在'})
                sampler = device['sampler']
                sample, containers = sampler.last
                if sample is None:
                    sample, containers = sampler.sample() or (None, [])
                if sample is None:
                    return self.reply(200, {'now': int(time.time()), 'info': {'hostname': device['name'], 'kind': device['kind']},
                                            'sample': None, 'containers': [], 'tail': None,
                                            'interval': device['interval'], 'devices': DEVICES.meta(),
                                            'device': {'name': device['name'], 'kind': device['kind']},
                                            'error': sampler.error})
                return self.reply(200, {'now': int(time.time()), 'info': device_info(device),
                                        'sample': sample, 'containers': containers,
                                        'tail': summarize_tail(STORE.tail(90, device['name'])),
                                        'interval': device['interval'], 'devices': DEVICES.meta(),
                                        'device': {'name': device['name'], 'kind': device['kind'],
                                                   'role': device.get('role', '')}})
            if path == '/history':
                query = urllib.parse.urlsplit(self.path).query
                params = urllib.parse.parse_qs(query)
                key = params.get('range', ['24h'])[0]
                if key not in RANGES:
                    return self.reply(400, {'error': 'range 参数不支持'})
                device_name = (params.get('device', [''])[0] or '')[:40]
                device = DEVICES.get(device_name or DEVICES.local_name)
                extras = bool(device and device['kind'] == 'nas')
                name = (params.get('container', [''])[0] or '')[:80]
                if name:
                    if not extras:
                        return self.reply(200, {'name': name, 't': [], 'cpu': [], 'mem': [], 'summary': None})
                    floor = (int(time.time()) - RANGES[key]) // 60 * 60
                    return self.reply(200, STORE.container_history(name, floor, STEPS[key]))
                return self.reply(200, STORE.history(key, device['name'] if device else '', extras))
            if path == '/targets' and self.command == 'PUT':
                length = int(self.headers.get('Content-Length', '0') or 0)
                if length > 256 * 1024:
                    return self.reply(413, {'error': 'targets too large'})
                payload = json.loads(self.rfile.read(length) or b'{}')
                count = PROBES.set_targets(payload.get('targets', []))
                return self.reply(200, {'ok': True, 'count': count})
            return self.reply(404, {'error': 'not found'})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except (ValueError, KeyError) as error:
            self.reply(400, {'error': str(error)})
        except Exception as error:
            print('Collector request failed:', type(error).__name__, flush=True)
            self.reply(500, {'error': 'collector unavailable'})


def summarize_tail(raw):
    """Compact recent raw samples for the vital-card sparklines."""
    tail = {'t': [], 'cpu': [], 'mem_used': [], 'net_rx': [], 'net_tx': [], 'io_r': [],
            'io_w': [], 'load': [], 'temp_cpu': [], 'primary': None}
    for sample in raw:
        tail['t'].append(sample['t'])
        tail['cpu'].append(sample.get('cpu'))
        mem = sample.get('mem')
        tail['mem_used'].append(mem.get('used') if mem else None)
        primary = sample.get('net_primary')
        net = (sample.get('net') or {}).get(primary) if primary else None
        tail['net_rx'].append(net.get('rx') if net else None)
        tail['net_tx'].append(net.get('tx') if net else None)
        tail['primary'] = tail['primary'] or primary
        busiest_io, busiest = None, -1
        for name, one in (sample.get('io') or {}).items():
            traffic = (one.get('r') or 0) + (one.get('w') or 0)
            if traffic > busiest:
                busiest_io, busiest = one, traffic
        tail['io_r'].append(busiest_io.get('r') if busiest_io else None)
        tail['io_w'].append(busiest_io.get('w') if busiest_io else None)
        load = sample.get('load')
        tail['load'].append(load['avg'][0] if load else None)
        temp = sample.get('temp') or {}
        tail['temp_cpu'].append(temp.get('cpu', temp.get('soc')))
    return tail


def collect_loop():
    while True:
        started = time.monotonic()
        try:
            LOCAL_SAMPLER.sample()
        except Exception as error:
            print('Sample round failed:', type(error).__name__, flush=True)
        rest = INTERVAL - (time.monotonic() - started)
        if rest > 0:
            time.sleep(rest)


def device_supervisor():
    """Pick up devices.json edits and start pollers for newly added boxes."""
    started = set()
    while True:
        try:
            DEVICES.reload()
            for name, entry in DEVICES.entries.items():
                if name not in started:
                    started.add(name)
                    threading.Thread(target=device_loop, args=(entry['sampler'],),
                                     daemon=True, name='device-' + name).start()
                    print(f'Polling {entry["sampler"].kind} device {name} '
                          f'({entry["config"]["host"]}) every {entry["sampler"].interval}s', flush=True)
        except Exception as error:
            print('Device supervisor failed:', type(error).__name__, flush=True)
        time.sleep(60)


def device_info(device):
    sampler = device['sampler']
    if device['kind'] == 'nas':
        info = {**INFO, 'kind': 'nas'}
    else:
        info = {**sampler.info, 'kind': sampler.kind}
    info['role'] = device.get('role', '')
    return info


STORE = None
LOCAL_SAMPLER = None
SAMPLER = None  # alias of LOCAL_SAMPLER for the health endpoint
PROBES = None
INFO = {}
DEVICES = None


def main():
    global STORE, LOCAL_SAMPLER, SAMPLER, PROBES, INFO, DEVICES
    STORE = Store(DB_PATH)
    PROBES = Prober(STORE)
    INFO = static_info()
    devices_config = load_devices_config()
    local_name = next((one['name'] for one in devices_config if one['kind'] == 'nas'),
                      INFO.get('hostname') or 'nas')
    LOCAL_SAMPLER = Sampler(local_name)
    SAMPLER = LOCAL_SAMPLER
    DEVICES = DeviceManager(local_name, LOCAL_SAMPLER)
    DEVICES.reload()
    remote = len(DEVICES.entries)
    print(f'Orbit collector {VERSION} on :{BIND_PORT}; device={local_name}; '
          f'remote={remote}; volumes={VOLUMES}', flush=True)
    threading.Thread(target=collect_loop, daemon=True, name='collect').start()
    threading.Thread(target=PROBES.run_forever, daemon=True, name='probe').start()
    threading.Thread(target=device_supervisor, daemon=True, name='devices').start()
    server = ThreadingHTTPServer(('0.0.0.0', BIND_PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == '__main__':
    main()
