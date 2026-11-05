"""Catalog hot reload: an owner-written data/seed.json merges over the bundled one.

Run locally: python3 -m unittest discover -s tests -p test_catalog_reload.py -v
All files, credentials and databases are created in temporary directories.
"""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SEED = json.loads((ROOT / 'seed.json').read_text())


def overlay_link(key, name, url, category_key='category:files', rank=5, visibility='public'):
    return {'catalog_key': key, 'category_key': category_key, 'name': name, 'url': url,
            'description': 'hot reload fixture', 'icon': 'link', 'visibility': visibility,
            'rank': rank, 'aliases': 'hot reload fixture', 'launch_mode': 'direct'}


def overlay_document(links, categories=None, settings=None):
    return {'settings': settings or {}, 'categories': categories or [], 'links': links}


class CatalogReloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='homehub-reload-')
        os.environ['HOMEHUB_DATA'] = cls.temp.name
        os.environ['HOMEHUB_ORIGIN'] = 'https://homehub.stream:8443'
        os.environ['HOMEHUB_COOKIE_SECURE'] = 'true'
        spec = importlib.util.spec_from_file_location('homehub_reload_under_test', ROOT / 'app.py')
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)
        cls.app.init()
        cls.password = (Path(cls.temp.name) / 'initial-admin-password').read_text().strip()
        cls.server = cls.app.Server(('127.0.0.1', 0), cls.app.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, method, path, data=None, cookie=None, csrf=None, headers=None, raw=False):
        h = {'Origin': 'https://homehub.stream:8443'}
        if cookie:
            h['Cookie'] = cookie
        if csrf:
            h['X-CSRF-Token'] = csrf
        h.update(headers or {})
        body = data if raw else (json.dumps(data).encode() if data is not None else None)
        if body is not None and not raw:
            h['Content-Type'] = 'application/json'
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request(method, path, body=body, headers=h)
        response = conn.getresponse()
        payload = response.read()
        status = response.status
        response_headers = dict(response.getheaders())
        conn.close()
        try:
            payload = json.loads(payload)
        except (ValueError, UnicodeError):
            pass
        return status, payload, response_headers

    def login(self):
        status, data, headers = self.request('POST', '/api/login',
                                             {'username': 'astral', 'password': self.password})
        self.assertEqual(status, 200, data)
        return headers['Set-Cookie'].split(';')[0], data['csrf']

    def write_overlay(self, content):
        path = Path(self.temp.name) / 'seed.json'
        path.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
        # Test-only: skip the one-second anti-thrash window of the change watcher.
        self.app.CATALOG_WATCH['at'] = 0.0
        return path

    def catalog(self):
        return self.request('GET', '/api/data')[1]

    def names(self, data):
        return {link['name'] for link in data['links']}

    def test_01_overlay_included_in_bootstrap_catalog(self):
        data = self.catalog()
        self.assertIn('MusicBrainz', self.names(data))
        # catalog() 是匿名视角：只含公开条目。
        self.assertEqual(len(data['links']),
                         sum(1 for x in SEED['links'] if x['visibility'] == 'public'))
        self.assertFalse((Path(self.temp.name) / 'seed.json').exists())

    def test_02_file_change_applies_without_restart(self):
        self.write_overlay(overlay_document([overlay_link('website:hot', '热加载服务', 'https://hot.homehub.stream:8443/')]))
        data = self.catalog()
        self.assertIn('热加载服务', self.names(data))
        self.assertEqual(len(data['links']),
                         sum(1 for x in SEED['links'] if x['visibility'] == 'public') + 1)
        self.assertIn('MusicBrainz', self.names(data))
        cookie, _ = self.login()
        status, catalog, _ = self.request('GET', '/api/catalog', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(catalog['overlay'])
        self.assertFalse(catalog['pending'])
        self.assertIsNone(catalog['error'])
        self.assertEqual(catalog['summary']['added'], 1)

    def test_03_invalid_overlay_is_rejected_and_previous_catalog_stays(self):
        self.write_overlay(overlay_document([overlay_link('website:orphan', '孤儿服务',
                                                          'https://orphan.homehub.stream:8443/',
                                                          category_key='category:missing')]))
        data = self.catalog()
        self.assertNotIn('孤儿服务', self.names(data))
        self.assertIn('热加载服务', self.names(data))
        cookie, _ = self.login()
        status, catalog, _ = self.request('GET', '/api/catalog', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(catalog['error'])
        broken = self.write_overlay('{not json')
        self.assertTrue(broken.exists())
        self.assertEqual(self.catalog()['links'][0]['name'] is not None, True)
        status, catalog, _ = self.request('GET', '/api/catalog', cookie=cookie)
        self.assertTrue(catalog['error'])

    def test_04_owner_edits_survive_every_reload(self):
        cookie, csrf = self.login()
        data = self.catalog()
        target = next(link for link in data['links'] if link['name'] == '热加载服务')
        status, _, _ = self.request('PUT', f"/api/links/{target['id']}",
                                 {'category_id': target['category_id'], 'name': '我改的名字',
                                  'url': target['url'], 'visibility': 'private'},
                                 cookie, csrf)
        self.assertEqual(status, 200)
        # Re-apply the very same overlay: the owner's rename and visibility must win.
        self.write_overlay(overlay_document([overlay_link('website:hot', '热加载服务', 'https://hot.homehub.stream:8443/')]))
        status, applied, _ = self.request('POST', '/api/catalog', cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200, applied)
        cookie, _ = self.login()
        status, me, _ = self.request('GET', '/api/data', cookie=cookie)
        kept = next(link for link in me['links'] if link['catalog_key'] == 'website:hot')
        self.assertEqual(kept['name'], '我改的名字')
        self.assertEqual(kept['visibility'], 'private')
        self.assertNotIn('我改的名字', {link['name'] for link in self.request('GET', '/api/data')[1]['links']})

    def test_05_upload_endpoint_requires_auth_and_validates(self):
        document = overlay_document([overlay_link('website:uploaded', '上传的服务', 'https://uploaded.homehub.stream:8443/',
                                                  category_key='category:system', rank=9)])
        body = json.dumps(document).encode()
        self.assertEqual(self.request('POST', '/api/catalog/seed', body, raw=True)[0], 401)
        cookie, csrf = self.login()
        self.assertEqual(self.request('POST', '/api/catalog/seed', body, cookie, csrf='wrong', raw=True)[0], 403)
        status, result, _ = self.request('POST', '/api/catalog/seed', b'{bad json', cookie, csrf, raw=True)
        self.assertEqual(status, 400, result)
        status, result, _ = self.request('POST', '/api/catalog/seed', b'x' * (1024 * 1024 + 1), cookie, csrf, raw=True)
        self.assertEqual(status, 413, result)
        status, result, _ = self.request('POST', '/api/catalog/seed', body, cookie, csrf, raw=True)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['summary']['added'], 1)
        self.assertIn('上传的服务', self.names(self.catalog()))
        self.assertTrue((Path(self.temp.name) / 'seed.json').is_file())
        self.assertEqual((Path(self.temp.name) / 'seed.json').stat().st_mode & 0o777, 0o600)

    def test_06_delete_overlay_keeps_services_and_falls_back(self):
        cookie, csrf = self.login()
        status, result, _ = self.request('DELETE', '/api/catalog/seed', cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200, result)
        self.assertIsNone(result['status']['overlay'])
        self.assertFalse((Path(self.temp.name) / 'seed.json').exists())
        self.assertTrue((Path(self.temp.name) / 'seed.json.bak').is_file())
        names = self.names(self.catalog())
        # Removing the overlay stops syncing it; services already in the portal stay.
        self.assertIn('上传的服务', names)
        self.assertIn('MusicBrainz', names)
        status, authenticated, _ = self.request('GET', '/api/data', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('我改的名字', {link['name'] for link in authenticated['links']})

    def test_07_sync_catalog_cli_uses_overlay_file(self):
        folder = tempfile.TemporaryDirectory(prefix='homehub-cli-')
        self.addCleanup(folder.cleanup)
        env = dict(os.environ, HOMEHUB_DATA=folder.name)
        first = subprocess.run([sys.executable, str(ROOT / 'app.py'), 'admin-password'],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(first.returncode, 0, first.stderr)
        (Path(folder.name) / 'seed.json').write_text(json.dumps(overlay_document([
            overlay_link('website:cli', 'CLI 服务', 'https://cli.homehub.stream:8443/', category_key='category:apps')])))
        second = subprocess.run([sys.executable, str(ROOT / 'app.py'), 'sync-catalog'],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(second.returncode, 0, second.stderr)
        summary = json.loads(second.stdout.strip().splitlines()[-1])
        self.assertEqual(summary['added'], 1)
        with sqlite3.connect(Path(folder.name) / 'homehub.db') as c:
            keys = {row[0] for row in c.execute('SELECT catalog_key FROM links')}
        self.assertIn('website:cli', keys)
        self.assertIn('website:musicbrainz', keys)


if __name__ == '__main__':
    unittest.main()
