"""Orbit collector tests: synthetic /proc & sysfs fixtures in temporary directories."""
from contextlib import contextmanager
import time

import http.client
import http.server
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('orbit_collector', ROOT / 'collector.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


@contextmanager
def fake_tree(tree):
    tmp = tempfile.TemporaryDirectory()
    base = Path(tmp.name)
    for name, content in tree.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    try:
        yield base
    finally:
        tmp.cleanup()


CPU_IDLE = 'cpu  100 0 100 700 0 0 0 0 0 0\n'
CPU_BUSY = 'cpu  200 0 100 800 0 0 0 0 0 0\n'
CORES_IDLE = 'cpu0 50 0 50 350 0 0 0 0 0 0\ncpu1 50 0 50 350 0 0 0 0 0 0\n'
CORES_BUSY = 'cpu0 110 0 50 410 0 0 0 0 0 0\ncpu1 110 0 50 410 0 0 0 0 0 0\n'


class ReaderTests(unittest.TestCase):
    def test_cpu_usage_between_samples(self):
        with fake_tree({'stat': CPU_IDLE + CORES_IDLE}) as base:
            sampler = collector.Sampler()
            with patch.object(collector, 'PROC', base):
                first = sampler.cpu()
                self.assertIsNone(first['usage'])
                self.assertEqual(len(first['cores']), 2)
            (base / 'stat').write_text(CPU_BUSY + CORES_BUSY)
            with patch.object(collector, 'PROC', base):
                second = sampler.cpu()
            self.assertAlmostEqual(second['usage'], 50.0, delta=0.5)
            self.assertAlmostEqual(second['cores'][0], 50.0, delta=2)

    def test_memory_parses_procinfo(self):
        meminfo = ('MemTotal:       32678832 kB\nMemFree:        1756160 kB\n'
                   'MemAvailable:  24446540 kB\nBuffers:         111608 kB\n'
                   'Cached:         20242552 kB\nSReclaimable:    1200000 kB\n'
                   'SwapTotal:      33554432 kB\nSwapFree:       32421888 kB\n')
        with fake_tree({'meminfo': meminfo}) as base:
            with patch.object(collector, 'PROC', base):
                mem = collector.Sampler.memory()
        self.assertEqual(mem['total'], 32678832 * 1024)
        self.assertEqual(mem['available'], 24446540 * 1024)
        self.assertAlmostEqual(mem['used'], (32678832 - 24446540) * 1024)
        self.assertEqual(mem['swap_used'], (33554432 - 32421888) * 1024)
        self.assertGreater(mem['cached'], 20 * 1024 * 1024 * 1024)

    def test_load_and_uptime(self):
        with fake_tree({'loadavg': '9.38 9.56 8.38 3/523 41234\n',
                        'uptime': '84840.25 160000.11'}) as base:
            with patch.object(collector, 'PROC', base):
                load = collector.Sampler.load()
                uptime = collector.Sampler.uptime()
        self.assertEqual(load['avg'], [9.38, 9.56, 8.38])
        self.assertEqual(load['procs'], 523)
        self.assertEqual(uptime, 84840)

    def test_diskstats_rates_skip_partitions(self):
        first = ('   7       0 sda 100 0 200 0 5 0 100 0 0 1000 0 0 0 0 0 0\n'
                 '   7       1 sda1 50 0 100 0 2 0 40 0 0 500 0 0 0 0 0 0\n')
        second = '   7       0 sda 200 0 400 0 25 0 300 0 0 2000 0 0 0 0 0 0\n'
        with fake_tree({'diskstats': first}) as base:
            sampler = collector.Sampler()
            with patch.object(collector, 'PROC', base):
                one = sampler.disks()
            (base / 'diskstats').write_text(second)
            with patch.object(collector, 'PROC', base):
                two = sampler.disks()
        self.assertIn('sda', one)
        self.assertNotIn('sda1', one)
        self.assertEqual(two['sda']['r'], (400 - 200) * 512)
        self.assertEqual(two['sda']['w'], (300 - 100) * 512)

    def test_network_reads_sysfs(self):
        tree = {
            'class/net/eth0/statistics/rx_bytes': '1000',
            'class/net/eth0/statistics/tx_bytes': '500',
            'class/net/eth0/operstate': 'up',
            'class/net/lo/statistics/rx_bytes': '9',
            'class/net/lo/statistics/tx_bytes': '9',
            'class/net/lo/operstate': 'unknown',
        }
        with tempfile.TemporaryDirectory() as sysdir:
            base = Path(sysdir)
            for name, content in tree.items():
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            sampler = collector.Sampler()
            with patch.object(collector, 'SYS', base):
                first = sampler.network()
            (base / 'class/net/eth0/statistics/rx_bytes').write_text('2000')
            (base / 'class/net/eth0/statistics/tx_bytes').write_text('1500')
            with patch.object(collector, 'SYS', base):
                second = sampler.network()
        self.assertIn('eth0', first)
        self.assertNotIn('lo', first)
        self.assertIsNone(first['eth0']['rx'])
        self.assertEqual(second['eth0']['rx'], 1000 / collector.INTERVAL)
        self.assertEqual(second['eth0']['tx'], 1000 / collector.INTERVAL)

    def test_container_usage_and_cpu_math(self):
        raw = {'cpu_stats': {'cpu_usage': {'total_usage': 300}, 'system_cpu_usage': 1000,
                             'online_cpus': 12},
               'memory_stats': {'usage': 1000, 'limit': 8000, 'stats': {'inactive_file': 200}},
               'networks': {'eth0': {'rx_bytes': 7, 'tx_bytes': 9}},
               'blkio_stats': {'io_service_bytes_recursive': [{'op': 'Read', 'value': 30},
                                                              {'op': 'Write', 'value': 40}]}}
        usage = collector.container_usage(raw)
        self.assertEqual(usage['mem'], 800)
        self.assertEqual(usage['rx'], 7)
        self.assertEqual(usage['blk_r'], 30)
        before = dict(usage, total=100, system=500)
        self.assertEqual(collector.cpu_percent(usage, before, 12), 480.0)
        self.assertIsNone(collector.cpu_percent(dict(usage, system=5), before, 12))
        self.assertIsNone(collector.container_usage(None))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'm.db'
        self.store = collector.Store(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def sample(ts, cpu=10.0, running=21):
        return {'t': ts, 'cpu': cpu, 'cores': [[cpu], [cpu / 2]],
                'mem': {'total': 32e9, 'used': 8e9, 'cached': 4e9, 'available': 20e9,
                        'swap_total': 32e9, 'swap_used': 1e9},
                'load': {'avg': [1.0, 2.0, 3.0], 'running': 3, 'procs': 500, 'last_pid': 1},
                'uptime': 1000,
                'net': {'eth0': {'rx': 100.0, 'tx': 50.0, 'operstate': 'up',
                                 'rx_bytes': 1e6, 'tx_bytes': 5e5, 'speed': 1000}},
                'net_primary': 'eth0',
                'io': {'sda': {'r': 10.0, 'w': 5.0, 'util': 1.0, 'reads': 1, 'writes': 1}},
                'vol': {'/vol1': {'total': 2e12, 'used': 6e11, 'free': 1.4e12}},
                'temp': {'cpu': 38.0},
                'docker': {'running': running, 'total': running, 'cpu': 100.0, 'mem': int(4e9)},
                'docker_error': None}

    def test_rollup_history_and_padding(self):
        now = 1_800_000_000
        base = now - 3600
        for i in range(120):
            ts = base + i * 30
            self.store.push(self.sample(ts, cpu=10.0 + i % 7), 'test')
            self.store.persist_containers(ts, [{'name': 'web', 'state': 'running',
                                                'cpu': 2.5, 'mem': int(1e8)}])
        for minute in range(base // 60 * 60, now // 60 * 60, 60):
            self.store.rollup('test', minute)
        hist = self.store.history('1h', 'test')
        times = hist['system']['t']
        self.assertTrue(times)
        size = len(times)
        self.assertEqual(len(hist['system']['cpu']), size)
        self.assertTrue(all(len(s) == size for s in hist['system']['cores']))
        self.assertTrue(all(len(v['rx']) == size for v in hist['system']['net'].values()))
        self.assertTrue(all(len(v['used']) == size for v in hist['system']['vol'].values()))
        self.assertTrue(all(all(len(x) == size for x in v.values())
                            for v in hist['system']['io'].values()))
        self.assertEqual(hist['containers'][0]['name'], 'web')
        self.assertAlmostEqual(hist['containers'][0]['avg_cpu'], 2.5, delta=0.01)

    def test_empty_history_is_safe(self):
        for key in ('1h', '14d'):
            hist = self.store.history(key)
            self.assertEqual(hist['system']['t'], [])
            self.assertEqual(hist['containers'], [])
            self.assertEqual(hist['probes'], [])

    def test_container_history(self):
        now = 1_800_000_000
        for i in range(10):
            ts = now - 600 + i * 60
            self.store.persist_containers(ts, [{'name': 'web', 'state': 'running',
                                                'cpu': i, 'mem': int(2e8 * i)}])
        data = self.store.container_history('web', now - 600, 60)
        self.assertEqual(len(data['t']), 10)
        self.assertEqual(data['cpu'][5], 5)
        self.assertEqual(data['summary']['name'], 'web')
        self.assertEqual(self.store.container_history('ghost', now - 600, 60)['t'], [])

    def test_prune_drops_expired_rows(self):
        now = int(time.time())
        old = now - collector.KEEP_MINUTES - 7200
        self.store.push(self.sample(old), 'test')
        self.store.rollup('test', old // 60 * 60)
        self.store.persist_containers(old, [{'name': 'old', 'state': 'running', 'cpu': 1.0, 'mem': 1}])
        self.store.prune()
        with collector.connect(self.db) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM samples').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM containers').fetchone()[0], 0)


class ProberTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = collector.Store(Path(self.tmp.name) / 'm.db')
        self.prober = collector.Prober(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_targets_validation_and_dedupe(self):
        count = self.prober.set_targets([
            {'key': 'k1', 'name': 'Ok', 'url': 'http://10.0.1.2:4533/'},
            {'name': 'NoScheme', 'url': 'ftp://bad/'},
            {'name': 'Dup', 'url': 'http://10.0.1.2:4533/x', 'key': 'k1'},
            {'name': 'NoUrl'},
        ])
        self.assertEqual(count, 1)
        self.assertEqual(self.prober.list_targets()[0][0], 'k1')
        self.prober.set_targets([{'name': 'Hashed', 'url': 'https://a.example/'}])
        self.assertTrue(self.prober.list_targets()[0][0].startswith('u'))

    def test_probe_once_against_local_server(self):
        class Stub(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'hello'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Stub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_address[1]
            result = self.prober.probe_once(f'http://127.0.0.1:{port}/health')
            self.assertTrue(result['ok'])
            self.assertEqual(result['code'], 200)
            self.assertGreaterEqual(result['ms'], 0)
            down = self.prober.probe_once('http://127.0.0.1:1/nope')
            self.assertFalse(down['ok'])
            self.assertIsNone(down['code'])
        finally:
            server.shutdown()
            server.server_close()

    def test_probe_via_keeps_host_header(self):
        seen = {}

        class Stub(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen['host'] = self.headers.get('Host')
                seen['path'] = self.path
                body = b'ok'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Stub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_address[1]
            with patch.object(collector, 'PROBE_VIA', f'http://127.0.0.1:{port}'):
                result = self.prober.probe_once('http://homehub.stream:8443/nav?q=1')
            self.assertTrue(result['ok'])
            self.assertEqual(seen['host'], 'homehub.stream:8443')
            self.assertEqual(seen['path'], '/nav?q=1')
        finally:
            server.shutdown()
            server.server_close()


MAC_REPORT = """ 2:20  up 23:34, 1 user, load averages: 2.31 2.15 1.98
---TOP
Processes: 624 total, 3 running, 621 sleeping, 0 threads
CPU usage: 5.14% user, 6.20% sys, 88.66% idle
CPU usage: 11.11% user, 9.09% sys, 79.80% idle
PhysMem: 12G used (2727M wired, 3319M compressor), 3874M unused.
---DF
Filesystem      1024-blocks      Used Available Capacity iused ifree %iused  Mounted on
/dev/disk3s1s1     470041856  98367488 356008128    22%  1001   511    0%   /
---NET
Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                       12345    0  220375743    12345    0  220375743     0
en0        1500  <Link#12>   aa:bb:cc:dd:ee:ff   98765    0 60353195809    54321    0 57477542104     0
utun3      1380  <Link#20>                       1234    0       8600     1234    0       8600     0
---ID
NoraMac
27.0.1
27.0.0
arm64
"""

OPENWRT_REPORT = """---STAT
cpu  100 0 100 700 0 0 0 0 0 0
cpu0 50 0 50 350 0 0 0 0 0 0
cpu1 50 0 50 350 0 0 0 0 0 0
cpu2 50 0 50 350 0 0 0 0 0 0
cpu3 50 0 50 350 0 0 0 0 0 0
intr 12345 0
ctxt 987654
btime 1700000000
processes 12345
procs_running 2
procs_blocked 0
---LOAD
1.20 0.85 0.60 2/214 4567
---MEM
MemTotal:        2048000 kB
MemFree:          512000 kB
MemAvailable:    1024000 kB
Buffers:           64000 kB
Cached:           256000 kB
SwapTotal:             0 kB
SwapFree:              0 kB
---NET
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 2203757  12345    0    0    0     0          0         0  2203757  12345    0    0    0     0       0          0
br-lan: 603531958  456789    0    0    0     0          0         0 574775421  345678    0    0    0     0       0          0
pppoe-wan: 57477542  123456    0    0    0     0          0         0 60353195  234567    0    0    0     0       0          0
---UP
84840.25 160000.11
---TEMP
52000
48500
---CONN
8123
16384
---DHCP
2
---LEASES
1793900000 aa:bb:cc:00:00:01 10.0.1.50 NoraMac 01:aa:bb:cc:00:00:01
1793900100 aa:bb:cc:00:00:02 10.0.1.51 living-room-robot *
100 aa:bb:cc:00:00:03 10.0.1.52 expired-host *
---NEIGH
10.0.1.1 dev br-lan lladdr aa:bb:cc:00:00:09 REACHABLE
10.0.1.8 dev br-lan lladdr aa:bb:cc:00:00:01 REACHABLE
10.0.1.60 dev br-lan lladdr aa:bb:cc:00:00:0a STALE
10.0.1.99 dev br-lan  FAILED
fe80::1 dev br-lan lladdr aa:bb:cc:00:00:09 STALE
---WAN
{
\t"up": true,
\t"pending": false,
\t"uptime": 84840,
\t"proto": "pppoe",
\t"device": "pppoe-wan",
\t"ipv4-address": [
\t\t{
\t\t\t"address": "60.177.124.16",
\t\t\t"mask": 32
\t\t}
\t]
}
---WIFI
---FIREWALL
firewall.cfg01dc25.default='defaults'
firewall.@redirect[0]=redirect
firewall.@redirect[0].name='HTTPS-8443'
firewall.@redirect[0].target='DNAT'
firewall.@redirect[0].src='wan'
firewall.@redirect[0].proto='tcp udp'
firewall.@redirect[0].src_dport='8443'
firewall.@redirect[0].dest_ip='10.0.1.2'
firewall.@redirect[0].dest_port='8443'
firewall.@redirect[1]=redirect
firewall.@redirect[1].name='qbit'
firewall.@redirect[1].target='DNAT'
firewall.@redirect[1].src='wan'
firewall.@redirect[1].src_dport='51413'
firewall.@redirect[1].dest_ip='10.0.1.2'
firewall.@redirect[1].dest_port='51413'
firewall.@zone[0]=zone
firewall.@zone[0].name='wan'
---BOARD
{
\t"kernel": "6.6.110",
\t"hostname": "R6S",
\t"model": "FriendlyElec NanoPi R6S",
\t"board_name": "friendlyarm,nanopi-r6s",
\t"release": {
\t\t"distribution": "ImmortalWrt",
\t\t"version": "24.10.4",
\t\t"revision": "r28427",
\t\t"target": "rockchip/armv8",
\t\t"description": "ImmortalWrt 24.10.4 r28427"
\t}
}
"""


class RemoteParserTests(unittest.TestCase):
    def test_parse_mac_report(self):
        sample = collector.parse_mac_report(MAC_REPORT)
        # The second top sample wins (first is since-boot).
        self.assertAlmostEqual(sample['cpu'], 20.2, delta=0.2)
        mem = sample['mem']
        self.assertEqual(mem['total'], 12 * 1024 ** 3 + 3874 * 1024 ** 2)
        self.assertEqual(mem['used'], 12 * 1024 ** 3)
        self.assertEqual(mem['available'], 3874 * 1024 ** 2)
        self.assertEqual(sample['load']['avg'], [2.31, 2.15, 1.98])
        self.assertEqual(sample['processes'], 624)
        self.assertEqual(sample['uptime'], 23 * 3600 + 34 * 60)
        self.assertIn('/', sample['vol'])
        self.assertEqual(sample['vol']['/']['fstype'], 'apfs')
        counters = sample['_net_counters']
        self.assertEqual(sorted(counters), ['en0'])
        self.assertEqual(counters['en0']['rx_bytes'], 60353195809.0)
        self.assertEqual(sample['_identity']['hostname'], 'NoraMac')
        self.assertEqual(sample['_identity']['arch'], 'arm64')

    def test_parse_openwrt_report(self):
        with patch.object(collector, 'time') as fake_time:
            fake_time.time.return_value = 1793899000
            fake_time.monotonic = collector.time.monotonic
            sample = collector.parse_openwrt_report(OPENWRT_REPORT)
        self.assertIsNone(sample['cpu'])  # counters for the next delta round
        self.assertEqual(len(sample['_cpu_counters'][1]), 4)
        self.assertEqual(sample['load']['avg'], [1.2, 0.85, 0.6])
        self.assertEqual(sample['mem']['total'], 2048000 * 1024)
        self.assertEqual(sample['mem']['available'], 1024000 * 1024)
        self.assertEqual(sample['uptime'], 84840)
        self.assertEqual(sample['temp'], {'soc': 52.0})
        self.assertEqual(sample['conntrack'], {'count': 8123, 'max': 16384})
        self.assertEqual(sample['dhcp_leases'], 2)
        self.assertEqual(sorted(sample['_net_counters']), ['br-lan', 'pppoe-wan'])
        self.assertEqual(sample['wan']['ip'], '60.177.124.16')
        self.assertTrue(sample['wan']['up'])
        self.assertEqual(sample['firmware'], 'ImmortalWrt 24.10.4 r28427')
        self.assertEqual(sample['model'], 'FriendlyElec NanoPi R6S')
        hosts = {one['ip']: one for one in sample['lan_hosts'] if one['ip']}
        self.assertEqual(hosts['10.0.1.50']['name'], 'NoraMac')
        self.assertEqual(hosts['10.0.1.50']['source'], 'dhcp')
        # static IP seen only in ARP still shows up
        self.assertEqual(hosts['10.0.1.60']['source'], 'arp')
        self.assertNotIn('10.0.1.99', hosts)  # FAILED neighbour entries are noise
        forwards = sample['port_forwards']
        self.assertEqual(len(forwards), 2)
        self.assertEqual(forwards[0]['name'], 'HTTPS-8443')
        self.assertEqual(forwards[0]['dest_ip'], '10.0.1.2')
        self.assertEqual(forwards[1]['src_port'], '51413')

    def test_human_bytes_and_uptime(self):
        self.assertEqual(collector.human_bytes('12G'), 12 * 1024 ** 3)
        self.assertEqual(collector.human_bytes('3874M'), 3874 * 1024 ** 2)
        self.assertIsNone(collector.human_bytes('abc'))
        self.assertEqual(collector.parse_uptime_text('up 3 days, 4:12, 1 user, load averages: 1 2 3'),
                         3 * 86400 + 4 * 3600 + 12 * 60)
        self.assertEqual(collector.parse_uptime_text('up 5 mins, 1 user, load averages: 1 2 3'), 300)
        self.assertEqual(collector.parse_uptime_text('up 23:34, 1 user, load averages: 1 2 3'),
                         23 * 3600 + 34 * 60)


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'm.db'
        self.store = collector.Store(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_history_is_device_scoped(self):
        now = 1_800_000_000
        base = now - 600
        for i in range(6):
            ts = base + i * 60
            self.store.push(self.store_sample(ts), 'Altair')
            self.store.push(self.store_sample(ts, cpu=50.0), 'NoraMac')
            self.store.rollup('Altair', ts)
            self.store.rollup('NoraMac', ts)
        nas = self.store.history('1h', 'Altair', extras=False)
        mac = self.store.history('1h', 'NoraMac', extras=False)
        self.assertTrue(nas['system']['t'])
        self.assertTrue(all(value < 20 for value in nas['system']['cpu'] if value is not None))
        self.assertTrue(all(value > 40 for value in mac['system']['cpu'] if value is not None))
        self.assertEqual(nas['containers'], [])
        self.assertEqual(nas['probes'], [])
        tail_nas = self.store.tail(10, 'Altair')
        tail_mac = self.store.tail(10, 'NoraMac')
        self.assertEqual(tail_nas[0]['cpu'], 10.0)
        self.assertEqual(tail_mac[0]['cpu'], 50.0)

    @staticmethod
    def store_sample(ts, cpu=10.0):
        return {'t': ts, 'cpu': cpu, 'cores': None, 'mem': None, 'load': None,
                'uptime': 10, 'net': None, 'net_primary': None, 'io': None,
                'vol': None, 'temp': None, 'docker': None, 'docker_error': None}

    def test_load_devices_config(self):
        config_file = Path(self.tmp.name) / 'devices.json'
        config_file.write_text(json.dumps({'devices': [
            {'name': 'Altair', 'kind': 'nas', 'role': 'NAS'},
            {'name': 'NoraMac', 'kind': 'mac', 'host': 'nora@10.0.1.8', 'role': 'Mac mini', 'interval': 15},
            {'name': 'R6S', 'kind': 'openwrt', 'host': 'root@10.0.1.1', 'role': 'Router', 'interval': 20},
            {'name': 'bad', 'kind': 'typo', 'host': 'x'},
            {'name': '', 'kind': 'mac', 'host': 'y'},
        ]}))
        with patch.object(collector, 'DEVICES_FILE', config_file):
            config = collector.load_devices_config()
        self.assertEqual([one['name'] for one in config], ['Altair', 'NoraMac', 'R6S'])
        with patch.object(collector, 'DEVICES_FILE', Path(self.tmp.name) / 'missing.json'):
            self.assertEqual(collector.load_devices_config(), [])

    def test_device_manager_meta(self):
        manager = collector.DeviceManager('Altair')
        manager.reload()
        meta = manager.meta()
        self.assertEqual(meta[0]['name'], 'Altair')
        self.assertEqual(meta[0]['kind'], 'nas')
        self.assertIs(manager.get('Altair')['sampler'], manager.get('Altair')['sampler'])
        self.assertIsNone(manager.get('Ghost'))


class CollectorApiTests(unittest.TestCase):
    @staticmethod
    def sample(ts):
        return {'t': ts, 'cpu': 5.0, 'cores': [[5.0]], 'mem': None, 'load': None, 'uptime': 10,
                'net': None, 'net_primary': None, 'io': None, 'vol': None, 'temp': None,
                'docker': None, 'docker_error': None}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'm.db'
        collector.STORE = collector.Store(self.db)
        collector.SAMPLER = collector.Sampler('altair')
        collector.PROBES = collector.Prober(collector.STORE)
        collector.DEVICES = collector.DeviceManager('altair', collector.SAMPLER)
        collector.INFO = {'hostname': 'altair', 'os': 'TestOS', 'kernel': '1.0'}
        self.server = collector.ThreadingHTTPServer(('127.0.0.1', 0), collector.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        connection.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                           headers=headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw) if raw else {}

    def test_health_snapshot_history_targets(self):
        status, body = self.request('GET', '/health')
        self.assertEqual(status, 200)
        self.assertTrue(body['ok'])
        status, body = self.request('GET', '/snapshot')
        self.assertEqual(status, 200)
        self.assertIn('sample', body)
        self.assertEqual(body['info']['hostname'], 'altair')
        status, body = self.request('GET', '/history?range=1h')
        self.assertEqual(status, 200)
        self.assertEqual(body['range'], '1h')
        status, _ = self.request('GET', '/history?range=2h')
        self.assertEqual(status, 400)
        status, body = self.request('PUT', '/targets', {'targets': [{'name': 'A', 'url': 'http://x/'}]})
        self.assertEqual(status, 200)
        self.assertEqual(body['count'], 1)
        status, body = self.request('GET', '/history?range=24h')
        self.assertEqual(body['probes'][0]['name'], 'A')
        status, _ = self.request('GET', '/nope')
        self.assertEqual(status, 404)

    def test_snapshot_serves_last_collected_sample(self):
        ts = 1_900_000_000
        sample = self.sample(ts)
        container = {'id': 'abc', 'name': 'web', 'image': 'nginx', 'state': 'running',
                     'status': 'Up', 'cpu': 1.0, 'mem': 100}
        collector.STORE.push(sample, 'altair')
        collector.SAMPLER.last = (sample, [container])
        status, body = self.request('GET', '/snapshot')
        self.assertEqual(status, 200)
        self.assertEqual(body['sample']['t'], ts)
        self.assertEqual(body['containers'][0]['name'], 'web')
        self.assertEqual(body['tail']['t'], [ts])


if __name__ == '__main__':
    unittest.main()
