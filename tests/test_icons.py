"""Focused icon regressions; all credentials, images and writable data are temporary.

Run from homehub: python3 -m unittest discover -s tests -p test_icons.py -v
No remote HTTP/DNS or production data is used.
"""
from contextlib import contextmanager
import base64
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import icon_library as icons


def chunk(tag, data):
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)


def png(width=1, height=1, raw=None):
    if raw is None:
        raw = (b'\0' + b'\x31\x72\xb3\xff' * width) * height
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


PNG = png()
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="#123456"/></svg>'
ICO = b'\0\0\1\0\1\0' + struct.pack('<BBBBHHII', 1, 1, 0, 0, 1, 32, len(PNG), 22) + PNG


# Small Pillow-generated real encodings; decoding these constants needs only stdlib.
JPEG = base64.b64decode('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAIAAgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAABf/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AIoA4Ff/2Q==')
PROGRESSIVE_JPEG = base64.b64decode('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wgARCAAIAAgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUAQEAAAAAAAAAAAAAAAAAAAAE/9oADAMBAAIQAxAAAAGIHC//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAn//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/AX//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/AX//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/An//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IX//2gAMAwEAAgADAAAAEPP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/EH//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/EH//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/EH//2Q==')
WEBP = base64.b64decode('UklGRjgAAABXRUJQVlA4ICwAAADwAQCdASoIAAgAAQAcJZgCdLoB+AAETAAA/vYTX/5Z8/hs6nP/kFywuuIwAA==')
LOSSLESS_WEBP = base64.b64decode('UklGRh4AAABXRUJQVlA4TBEAAAAvB8ABEAfQqo5UsLSBiOh/AAA=')

class Response:
    def __init__(self, body=PNG, status=200, headers=None):
        self.status = status
        self.headers = {'Content-Type': 'image/png', **(headers or {})}
        self.stream = io.BytesIO(body)
        self.read_sizes = []

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        self.read_sizes.append(size)
        return self.stream.read(size)


def dns_rows(*addresses):
    return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 80)) for ip in addresses]


@contextmanager
def transport(*responses, addresses=('10.1.2.3',)):
    connections = []
    def connection(*args):
        conn = Mock()
        conn.getresponse.return_value = responses[len(connections)]
        conn.constructor_args = args
        connections.append(conn)
        return conn
    with patch.object(icons.socket, 'getaddrinfo', return_value=dns_rows(*addresses)) as dns, \
            patch.object(icons, 'PinnedHTTP', side_effect=connection), \
            patch.object(icons, 'PinnedHTTPS', side_effect=connection), \
            patch.object(icons.threading, 'Timer'):
        yield connections, dns


class IconUnitTests(unittest.TestCase):
    def test_real_jpeg_progressive_and_webp_encodings(self):
        for image, ext in ((JPEG, '.jpg'), (PROGRESSIVE_JPEG, '.jpg'), (WEBP, '.webp'), (LOSSLESS_WEBP, '.webp')):
            with self.subTest(ext=ext, size=len(image)):
                self.assertEqual(icons.validate_image(image), ext)

    def test_valid_png_svg_and_real_png_ico(self):
        for image, ext in ((PNG, '.png'), (SVG, '.svg'), (ICO, '.ico')):
            with self.subTest(ext=ext):
                self.assertEqual(icons.validate_image(image), ext)

    def test_malformed_formats_and_png_crc(self):
        bad_crc = PNG[:-1] + bytes([PNG[-1] ^ 1])
        for image in (b'<html>not an image</html>', PNG[:-12], bad_crc,
                      b'\xff\xd8' + b'bad jpeg' * 5 + b'\xff\xd9',
                      b'RIFF' + b'\0' * 40, ICO[:-1], b'GIF89a' + b'\0' * 30):
            with self.subTest(signature=image[:8]), self.assertRaises(icons.IconError):
                icons.validate_image(image)

    def test_size_pixel_and_png_decode_budgets(self):
        for image in (PNG + b'x' * icons.MAX_BYTES, png(4097, 1),
                      png(2048, 2048, b'\0'), png(raw=b'\5\0\0\0\0'), png(raw=b'\0')):
            with self.subTest(size=len(image)), self.assertRaises(icons.IconError):
                icons.validate_image(image)

    def test_svg_active_external_and_dtd_rejected(self):
        cases = [b'<svg><script>alert(1)</script></svg>',
                 b'<svg onload="alert(1)"><rect/></svg>',
                 b'<!DOCTYPE svg SYSTEM "https://example.invalid/x"><svg/>',
                 b'<svg><use href="https://example.invalid/x"/></svg>',
                 b'<svg><rect style="fill:url(https://example.invalid/x)"/></svg>',
                 b'<svg><foreignObject><div/></foreignObject></svg>']
        for image in cases:
            with self.subTest(image=image), self.assertRaises(icons.IconError):
                icons.validate_image(image)
        self.assertEqual(icons.validate_image(b'<svg><defs><path id="x"/></defs><use href="#x"/></svg>'), '.svg')

    def test_svg_node_and_depth_limits(self):
        for image in (b'<svg>' + b'<g/>' * 10000 + b'</svg>',
                      b'<svg>' + b'<g>' * 64 + b'</g>' * 64 + b'</svg>'):
            with self.assertRaises(icons.IconError):
                icons.validate_image(image)
        self.assertEqual(icons.validate_image(b'<svg>' + b'<g/>' * 9999 + b'</svg>'), '.svg')

    def test_address_policy_allows_lan_blocks_metadata_and_reserved(self):
        for ip in ('10.0.1.2', '172.16.1.2', '192.168.1.2', '8.8.8.8'):
            with self.subTest(ip=ip):
                self.assertTrue(icons.allowed_address(ip))
        for ip in ('127.0.0.1', '169.254.169.254', '0.0.0.0', '240.0.0.1', '::1',
                   'fe80::1', 'fd00:ec2::254', '::ffff:169.254.169.254', '::ffff:127.0.0.1'):
            with self.subTest(ip=ip):
                self.assertFalse(icons.allowed_address(ip))

    def test_dns_mixed_safe_and_unsafe_answers_rejected(self):
        for ip in ('169.254.169.254', '127.0.0.1', '::ffff:169.254.169.254', 'fd00:ec2::254'):
            with self.subTest(ip=ip), patch.object(icons.socket, 'getaddrinfo', return_value=dns_rows('10.0.1.2', ip)):
                with self.assertRaises(icons.IconError):
                    icons.resolve_target('fixture.invalid', 80)

    def test_pinned_socket_never_re_resolves_hostname_and_tls_keeps_sni(self):
        with patch.object(icons.socket, 'create_connection') as connect, \
                patch.object(icons.socket, 'getaddrinfo', side_effect=AssertionError('rebind')), \
                patch.object(icons.ssl, 'create_default_context') as tls:
            conn = icons.PinnedHTTPS('fixture.invalid', 443, '10.0.1.2', 3)
            conn.connect()
            connect.assert_called_once_with(('10.0.1.2', 443), 3)
            tls.return_value.wrap_socket.assert_called_once_with(connect.return_value, server_hostname='fixture.invalid')

    def test_fetch_bytes_bounded_reads_headers_and_redirect_pinning(self):
        first = Response(status=302, headers={'Location': 'https://other.invalid/icon?q=1'})
        last = Response(headers={'Content-Length': str(len(PNG))})
        with transport(first, last) as (connections, dns):
            self.assertEqual(icons.fetch_remote('http://fixture.invalid/start'), PNG)
        self.assertEqual(dns.call_count, 2)
        self.assertEqual(connections[1].constructor_args[:3], ('other.invalid', 443, '10.1.2.3'))
        connections[1].putrequest.assert_called_once_with('GET', '/icon?q=1', skip_host=True, skip_accept_encoding=True)
        self.assertIn(unittest.mock.call('Accept-Encoding', 'identity'), connections[1].putheader.call_args_list)
        self.assertTrue(all(0 < size <= 65536 for size in last.read_sizes))
        for conn in connections:
            conn.close.assert_called_once()

    def test_fetch_rejects_bad_headers_incomplete_and_overflow(self):
        cases = [Response(headers={'Content-Type': 'text/html'}),
                 Response(headers={'Content-Encoding': 'gzip'}),
                 Response(headers={'Content-Length': str(icons.MAX_BYTES + 1)}),
                 Response(headers={'Content-Length': 'bogus'}),
                 Response(headers={'Content-Length': str(len(PNG) + 1)}),
                 Response(b'x' * (icons.MAX_BYTES + 1)), Response(status=403)]
        for response in cases:
            with self.subTest(headers=response.headers, status=response.status), transport(response):
                with self.assertRaises(icons.IconError):
                    icons.fetch_remote('http://fixture.invalid/icon')

    def test_fetch_redirect_to_metadata_and_redirect_loop_rejected(self):
        response = Response(status=302, headers={'Location': 'http://169.254.169.254/latest'})
        with transport(response) as (connections, dns):
            dns.side_effect = [dns_rows('10.0.1.2'), dns_rows('169.254.169.254')]
            with self.assertRaises(icons.IconError):
                icons.fetch_remote('http://fixture.invalid/icon')
            self.assertEqual(len(connections), 1)
        redirects = [Response(status=302, headers={'Location': '/again'}) for _ in range(5)]
        with transport(*redirects), self.assertRaises(icons.IconError):
            icons.fetch_remote('http://fixture.invalid/icon')

    def test_fetch_timeout_and_invalid_url_contract(self):
        conn = Mock()
        conn.connect.side_effect = socket.timeout('fixture timeout')
        with transport(Response()), patch.object(icons, 'PinnedHTTP', return_value=conn):
            with self.assertRaises(icons.IconError):
                icons.fetch_remote('http://fixture.invalid/icon')
        conn.close.assert_called_once()
        for target in ('file:///tmp/icon', 'http://user:pass@fixture.invalid/x',
                       'http://fixture.invalid/x#fragment', 'http://fixture.invalid:99999/x'):
            with self.subTest(target=target), self.assertRaises(icons.IconError):
                icons.validate_target(target)


class IconAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='homehub-icons-tests-')
        self.folder = Path(self.temp.name).resolve()
        self.passwords = {'astral': secrets.token_urlsafe(24), 'icon-editor': secrets.token_urlsafe(24)}
        pending = self.folder / 'initial-admin-password'
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(self.passwords['astral'] + '\n')
        with patch.dict(os.environ, {'HOMEHUB_DATA': str(self.folder), 'HOMEHUB_ORIGIN': 'https://icons.test', 'HOMEHUB_COOKIE_SECURE': 'true'}):
            spec = importlib.util.spec_from_file_location('icons_app_under_test', ROOT / 'app.py')
            self.app = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.app)
        self.app.init()
        self.app.STATIC = self.folder / 'static'
        (self.app.STATIC / 'icons').mkdir(parents=True)
        (self.app.STATIC / 'icons' / 'builtin.svg').write_bytes(SVG)
        with self.app.db() as c:
            c.execute('INSERT INTO accounts(username,password,display) VALUES(?,?,?)',
                      ('icon-editor', self.app.password_hash(self.passwords['icon-editor']), ''))
        self.server = self.app.Server(('127.0.0.1', 0), self.app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.auth = self.login('astral')
        self.category = self.request('GET', '/api/data')[1]['categories'][0]['id']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        # TemporaryDirectory's recursive cleanup is restricted to this verified fixture.
        self.assertEqual(Path(self.temp.name).resolve(), self.folder)
        self.assertEqual(self.folder.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.folder.name.startswith('homehub-icons-tests-'))
        self.temp.cleanup()

    def request(self, method, path, data=None, auth=None, headers=None):
        h = {'Origin': 'https://icons.test'}
        if auth:
            h['Cookie'], h['X-CSRF-Token'] = auth
        if isinstance(data, bytes):
            body = data
        else:
            body = json.dumps(data).encode() if data is not None else None
            if body is not None:
                h['Content-Type'] = 'application/json'
        h.update(headers or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        try:
            conn.request(method, path, body=body, headers=h)
            response = conn.getresponse()
            result = response.read()
            status, response_headers = response.status, dict(response.getheaders())
        finally:
            conn.close()
        try:
            result = json.loads(result)
        except (ValueError, UnicodeError):
            pass
        return status, result, response_headers

    def login(self, username):
        status, data, headers = self.request('POST', '/api/login', {'username': username, 'password': self.passwords[username]})
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';')[0], data['csrf']

    def upload(self, image=PNG, filename='fixture.png', auth=None):
        status, result, _ = self.request('POST', '/api/icons', image, auth or self.auth, {'X-Filename': filename})
        self.assertEqual(status, 200, result)
        return result

    def create(self, reference='', **fields):
        item = dict(category_id=self.category, name='Icon fixture', url='https://example.invalid/', visibility='public', icon='star', iconUrl=reference)
        item.update(fields)
        status, result, _ = self.request('POST', '/api/links', item, self.auth)
        self.assertEqual(status, 201, result)
        return result['id']

    def link(self, ident, auth=None):
        return next(row for row in self.request('GET', '/api/data', auth=auth)[1]['links'] if row['id'] == ident)

    def test_auth_csrf_and_origin_before_icon_processing(self):
        with patch.object(self.app, 'fetch_remote_icon') as fetch:
            self.assertEqual(self.request('GET', '/api/icons')[0], 401)
            for auth, headers, expected in ((None, {}, 401), ((self.auth[0], ''), {}, 403),
                                             (self.auth, {'Origin': 'https://evil.invalid'}, 403)):
                self.assertEqual(self.request('POST', '/api/icons', {'url': 'https://fixture.invalid/x'}, auth, headers)[0], expected)
            fetch.assert_not_called()
        self.assertEqual(self.request('GET', '/api/icons', auth=self.auth)[1], {'library': []})

    def test_raw_upload_dedupe_listing_and_local_media(self):
        uploaded = self.upload()
        expected = hashlib.sha256(PNG).hexdigest()[:32] + '.png'
        self.assertEqual(uploaded['icon'], expected)
        self.assertEqual(uploaded['url'], '/media/icons/' + expected)
        self.assertEqual(self.upload()['icon'], expected)
        self.assertEqual(self.request('GET', '/api/icons', auth=self.auth)[1]['library'], [expected])
        status, body, headers = self.request('GET', uploaded['url'])
        self.assertEqual((status, body), (200, PNG))
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual((icons.library_dir(self.folder) / expected).stat().st_mode & 0o777, 0o600)

    def test_json_remote_import_uses_bytes_wrapper(self):
        target = 'https://fixture.invalid/icon'
        with patch.object(self.app, 'fetch_remote_icon', return_value=PNG) as fetch:
            status, result, _ = self.request('POST', '/api/icons', {'url': target}, self.auth)
        self.assertEqual(status, 200, result)
        fetch.assert_called_once_with(target)
        self.assertEqual(self.request('GET', result['url'])[1], PNG)
        with patch.object(icons, 'fetch_remote', return_value=PNG):
            self.assertIsInstance(self.app.fetch_remote_icon(target), bytes)
            self.assertEqual(self.app.fetch_remote_icon(target), PNG)

    def test_real_jpeg_and_webp_uploads_and_mime(self):
        cases = ((JPEG, 'fixture.JPG', 'image/jpeg'), (PROGRESSIVE_JPEG, 'fixture.jpeg', 'image/jpeg'),
                 (WEBP, 'fixture.webp', 'image/webp'), (LOSSLESS_WEBP, 'alpha.webp', 'image/webp'))
        for image, filename, mime in cases:
            with self.subTest(filename=filename):
                uploaded = self.upload(image, filename)
                status, body, headers = self.request('GET', uploaded['url'])
                self.assertEqual((status, body), (200, image))
                self.assertEqual(headers['Content-Type'], mime)

    def test_svg_and_ico_upload_and_svg_security_headers(self):
        for image, filename in ((SVG, 'fixture.svg'), (ICO, 'fixture.ico')):
            uploaded = self.upload(image, filename)
            self.assertEqual(self.request('GET', uploaded['url'])[1], image)
        for path in (self.upload(SVG, 'fixture.svg')['url'], '/assets/icons/builtin.svg'):
            status, _, headers = self.request('GET', path)
            self.assertEqual(status, 200)
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn('sandbox', headers['Content-Security-Policy'])
            self.assertIn("script-src 'none'", headers['Content-Security-Policy'])

    def test_public_override_builtin_selection_and_restore_default(self):
        uploaded = self.upload()
        ident = self.create(uploaded['url'])
        row = self.link(ident)
        self.assertEqual(row['iconUrl'], uploaded['url'])
        for reference in ('/assets/icons/builtin.svg', ''):
            row['iconUrl'] = reference
            self.assertEqual(self.request('PUT', f'/api/links/{ident}', row, self.auth)[0], 200)
            self.assertEqual(self.link(ident)['iconUrl'], reference)
            self.assertEqual(self.link(ident)['icon'], 'star')

    def test_legacy_put_preserves_override_and_name_reference_canonicalized(self):
        uploaded = self.upload()
        ident = self.create(uploaded['icon'])
        row = self.link(ident)
        self.assertEqual(row['iconUrl'], uploaded['url'])
        row.pop('iconUrl')
        row.pop('icon_url', None)
        row['name'] = 'Legacy edit'
        self.assertEqual(self.request('PUT', f'/api/links/{ident}', row, self.auth)[0], 200)
        self.assertEqual(self.link(ident)['iconUrl'], uploaded['url'])

    def test_invalid_paths_roll_back_link_post_and_put(self):
        ident = self.create(self.upload()['url'])
        before = self.request('GET', '/api/export', auth=self.auth)[1]
        row = self.link(ident)
        for reference in ('/vol1/1000/private.png', '../fixture.png', '/media/icons/../fixture.png',
                          '/media/icons/%2e%2e/fixture.png', '/assets/icons/../builtin.svg',
                          'https://example.invalid/icon.png', '/media/icons/missing.png'):
            with self.subTest(reference=reference):
                bad = dict(row, name='Must roll back', iconUrl=reference)
                self.assertEqual(self.request('PUT', f'/api/links/{ident}', bad, self.auth)[0], 400)
                self.assertEqual(self.request('POST', '/api/links', bad, self.auth)[0], 400)
                self.assertEqual(self.request('GET', '/api/export', auth=self.auth)[1], before)

    def test_export_import_preserves_refs_missing_icons_fall_back(self):
        uploaded = self.upload()
        self.create(uploaded['url'], name='Uploaded reference')
        self.create('/assets/icons/builtin.svg', name='Builtin reference')
        backup = self.request('GET', '/api/export', auth=self.auth)[1]
        self.assertEqual(self.request('POST', '/api/import', backup, self.auth)[0], 200)
        rows = self.request('GET', '/api/data')[1]['links']
        self.assertEqual(next(x for x in rows if x['name'] == 'Uploaded reference')['iconUrl'], uploaded['url'])
        self.assertEqual(next(x for x in rows if x['name'] == 'Builtin reference')['iconUrl'], '/assets/icons/builtin.svg')
        broken = json.loads(json.dumps(backup))
        target = next(x for x in broken['links'] if x['name'] == 'Uploaded reference')
        target['iconUrl'] = '/media/icons/missing.png'
        status, result, _ = self.request('POST', '/api/import', broken, self.auth)
        self.assertEqual(status, 200, result)
        self.assertTrue(result['missingIcons'])
        rows = self.request('GET', '/api/data')[1]['links']
        self.assertEqual(next(x for x in rows if x['name'] == 'Uploaded reference')['iconUrl'], '')

    def test_backup_explicit_default_beats_legacy_reference(self):
        uploaded = self.upload()
        self.create(uploaded['url'], name='Explicit default fixture')
        backup = self.request('GET', '/api/export', auth=self.auth)[1]
        target = next(x for x in backup['links'] if x['name'] == 'Explicit default fixture')
        self.assertTrue(target['icon_url'])
        target['iconUrl'] = ''
        status, result, _ = self.request('POST', '/api/import', backup, self.auth)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['missingIcons'], [])
        rows = self.request('GET', '/api/data')[1]['links']
        self.assertEqual(next(x for x in rows if x['name'] == 'Explicit default fixture')['iconUrl'], '')
        target.pop('iconUrl')
        self.assertEqual(self.request('POST', '/api/import', backup, self.auth)[0], 200)
        rows = self.request('GET', '/api/data')[1]['links']
        self.assertEqual(next(x for x in rows if x['name'] == 'Explicit default fixture')['iconUrl'], uploaded['url'])

    def test_symlink_library_file_rejected_for_list_media_and_reference(self):
        directory = icons.library_dir(self.folder, True)
        target = self.folder / 'outside.png'
        target.write_bytes(PNG)
        (directory / 'linked.png').symlink_to(target)
        self.assertNotIn('linked.png', self.request('GET', '/api/icons', auth=self.auth)[1]['library'])
        self.assertEqual(self.request('GET', '/media/icons/linked.png')[0], 404)
        item = dict(category_id=self.category, name='Symlink', url='https://example.invalid/', iconUrl='/media/icons/linked.png')
        self.assertEqual(self.request('POST', '/api/links', item, self.auth)[0], 400)

    def test_symlink_storage_directory_rejected_without_touching_target(self):
        media = self.folder / 'media'
        media.mkdir(exist_ok=True)
        outside = self.folder / 'outside'
        outside.mkdir()
        (media / 'icons').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.request('POST', '/api/icons', PNG, self.auth, {'X-Filename': 'fixture.png'})[0], 400)
        self.assertEqual(list(outside.iterdir()), [])

    def test_upload_malformed_mismatch_and_oversize_do_not_write(self):
        before = self.request('GET', '/api/icons', auth=self.auth)[1]
        for body, headers, expected in ((b'<svg><script/></svg>' * 2, {'X-Filename': 'bad.svg'}, 400),
                                         (PNG, {'X-Filename': 'bad.svg'}, 400),
                                         (PNG, {'X-Filename': 'bad.exe'}, 400),
                                         (b'', {'X-Filename': 'large.png', 'Content-Length': str(icons.MAX_BYTES + 1)}, 413)):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('POST', '/api/icons', body, self.auth, headers)[0], expected)
                self.assertEqual(self.request('GET', '/api/icons', auth=self.auth)[1], before)

    def test_operation_slots_limit_both_upload_and_remote_to_429(self):
        with icons.operation(), icons.operation(), patch.object(self.app, 'fetch_remote_icon') as fetch:
            for body, headers in ((PNG, {'X-Filename': 'fixture.png'}), ({'url': 'https://fixture.invalid/icon'}, {})):
                self.assertEqual(self.request('POST', '/api/icons', body, self.auth, headers)[0], 429)
            fetch.assert_not_called()
        self.upload()  # Slots must be released after the busy path.

    def test_second_account_edits_shared_icons_without_changing_accounts(self):
        with self.app.db() as c:
            before = [tuple(row) for row in c.execute('SELECT * FROM accounts ORDER BY id')]
        uploaded = self.upload()
        ident = self.create(uploaded['url'])
        other = self.login('icon-editor')
        self.assertIn(uploaded['icon'], self.request('GET', '/api/icons', auth=other)[1]['library'])
        self.upload(SVG, 'other.svg', other)
        row = self.link(ident, other)
        row['iconUrl'] = '/assets/icons/builtin.svg'
        self.assertEqual(self.request('PUT', f'/api/links/{ident}', row, other)[0], 200)
        self.assertEqual(self.link(ident)['iconUrl'], '/assets/icons/builtin.svg')
        with self.app.db() as c:
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM accounts ORDER BY id')], before)
            self.assertEqual(c.execute('SELECT count(*) FROM favorites').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT count(*) FROM prefs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
