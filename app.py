#!/usr/bin/env python3
"""Homehub: dependency-free, single-admin portal service. Python 3.12+."""
import base64
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import icon_library
import catalog

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('HOMEHUB_DATA', '/data'))
STATIC = ROOT / 'static'
VERSION = (ROOT / 'VERSION').read_text().strip() if (ROOT / 'VERSION').is_file() else '1.0.0'
ASSET_VERSION = hashlib.sha256(b''.join((STATIC / name).read_bytes() for name in ('app.js','styles.css','icons.js'))).hexdigest()[:16]
DB = DATA / 'homehub.db'
ORIGIN = os.environ.get('HOMEHUB_ORIGIN', 'https://homehub.stream:8443').rstrip('/')
COOKIE_SECURE = os.environ.get('HOMEHUB_COOKIE_SECURE', 'true').lower() == 'true'
SESSION_SECONDS = 7 * 24 * 3600
COOKIE = '__Host-homehub' if COOKIE_SECURE else 'homehub-dev'
MAX_JSON = 1024 * 1024
MAX_BACKUP = 32 * 1024 * 1024
MAX_AUDIO = 25 * 1024 * 1024
AUTH_LOCK = threading.Lock()
ATTEMPTS = {}

class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

@contextmanager
def db():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def password_hash(password):
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return 'scrypt$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(value).decode()

def check_password(password, encoded):
    try:
        _, salt, value = encoded.split('$')
        value = base64.b64decode(value)
        result = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(result, value)
    except (ValueError, TypeError):
        return False

def text(value, label, maxlen=200, required=False):
    if not isinstance(value, str) or len(value) > maxlen or (required and not value.strip()):
        raise APIError(400, f'{label}格式不正确（最多 {maxlen} 字符）')
    return value.strip()

def url(value, allow_empty=False, local_media=False):
    value = text(value, '链接', 2048)
    if allow_empty and not value:
        return ''
    if local_media and re.fullmatch(r'/media/music\.(mp3|ogg|wav|m4a|opus)(?:\?v=[0-9a-f]{16})?', value):
        return value
    try:
        u = urlsplit(value)
        if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password:
            raise ValueError()
        if local_media and u.scheme != 'https':
            raise APIError(400, '外部音乐地址须使用 HTTPS，或上传本站音频')
        _ = u.port
        if any(ord(ch) < 32 for ch in value) or '\\' in value:
            raise ValueError()
    except ValueError:
        raise APIError(400, '请输入完整的 http:// 或 https:// 链接，不可包含账号密码')
    return value

def rank(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not -100000 <= value <= 100000:
        raise APIError(400, '排序值应为 -100000 到 100000 之间的数字')
    return int(value)

def category_fields(v):
    return {'name': text(v.get('name', ''), '分类名称', 40, True), 'rank': rank(v.get('rank', 0))}

def link_fields(v, c):
    category_id = v.get('category_id')
    if isinstance(category_id, bool) or not isinstance(category_id, int) or not c.execute('SELECT 1 FROM categories WHERE id=?', (category_id,)).fetchone():
        raise APIError(400, '请选择有效分类')
    visibility = v.get('visibility', 'private')
    if visibility not in ('public', 'private'):
        raise APIError(400, '可见性必须为 public 或 private')
    launch_mode = v.get('launch_mode', 'direct')
    if launch_mode not in ('direct', 'fnos'):
        raise APIError(400, '启动方式必须为 direct 或 fnos')
    pinned = v.get('pinned', False)
    if not isinstance(pinned, bool):
        raise APIError(400, '常用标记必须为布尔值')
    return {'category_id':category_id, 'name':text(v.get('name', ''), '名称', 80, True),
            'url':url(v.get('url', '')), 'description':text(v.get('description', ''), '描述', 200),
            'icon':text(v.get('icon', 'link'), '图标', 40), 'visibility':visibility,
            'pinned':int(pinned), 'rank':rank(v.get('rank', 0)), 'aliases':text(v.get('aliases', ''), '搜索别名', 200),
            'launch_mode': launch_mode}

def settings_fields(v, old):
    if not isinstance(v, dict):
        raise APIError(400, '设置格式不正确')
    out = dict(old)
    if 'musicAutoplay' in v and not isinstance(v['musicAutoplay'], bool):
        raise APIError(400, '音乐自动播放设置必须为布尔值')
    # Legacy settings clients must not reset a newer autoplay preference.
    out['musicAutoplay'] = v.get('musicAutoplay', old.get('musicAutoplay', False))
    for key, limit in [('title', 40), ('tagline', 100), ('subtitle', 200), ('musicTitle', 100)]:
        if key in v:
            out[key] = text(v[key], key, limit, key == 'title')
    if 'musicUrl' in v:
        out['musicUrl'] = url(v['musicUrl'], allow_empty=True, local_media=True)
    return out

def imported_key(value):
    if value is not None and not catalog.valid_key(value):
        raise APIError(400, '备份目录标识格式不正确')
    return value

ICON_NAME = icon_library.NAME

def icon_library_dir(create=False):
    return icon_library.library_dir(DATA, create)

def save_icon_bytes(b):
    return icon_library.store_image(DATA, b)

def fetch_remote_icon(target):
    return icon_library.fetch_remote(target)

def resolve_icon(value, missing_ok=False):
    return icon_library.resolve_reference(value, DATA, STATIC, missing_ok)


CATALOG_SEED_LIMIT = 1024 * 1024
CATALOG_LOCK = threading.Lock()
CATALOG_WATCH = {'at': 0.0}


def catalog_files():
    """Return (bundled catalog, optional overlay written by the owner in DATA)."""
    overlay = DATA / 'seed.json'
    if overlay.is_symlink():
        raise ValueError('data/seed.json 不能是符号链接')
    return ROOT / 'seed.json', (overlay if overlay.is_file() else None)


def catalog_stamp(*paths):
    stamps = []
    for path in paths:
        if path is None:
            stamps.append(None)
        else:
            stat = path.stat()
            stamps.append([stat.st_size, stat.st_mtime_ns])
    return stamps


def load_catalog(base=None, overlay=None):
    """Parse, validate and merge the bundled catalog with the optional overlay."""
    base, overlay = catalog_files() if base is None else (base, overlay)
    documents = []
    for path in (base, overlay):
        if path is None:
            continue
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except (UnicodeError, ValueError, OSError) as error:
            raise ValueError(f'{path.name} 不是有效的 JSON 目录：{error}') from error
        catalog.validate_seed(document)
        documents.append(document)
    return catalog.merge(*documents)


def read_catalog_state():
    try:
        with db() as c:
            row = c.execute('SELECT data FROM catalog_state WHERE id=1').fetchone()
        return json.loads(row['data']) if row else {}
    except (sqlite3.Error, ValueError):
        return {}


def write_catalog_state(state):
    with db() as c:
        c.execute('INSERT INTO catalog_state(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                  (json.dumps(state, ensure_ascii=False),))


def apply_catalog(force=False, publish_existing=False):
    """Sync the current catalog files into SQLite; safe to call on every request."""
    with CATALOG_LOCK:
        base, overlay = catalog_files()
        stamp = catalog_stamp(base, overlay)
        state = read_catalog_state()
        if not force and state.get('stamp') == stamp:
            return {'ok': True, 'changed': False, 'summary': state.get('summary')}
        try:
            seed = load_catalog(base, overlay)
        except (ValueError, OSError) as error:
            previous = read_catalog_state()
            failed = {**previous, 'stamp': stamp, 'error': str(error), 'errorAt': time.time(),
                      'source': str(overlay or base)}
            write_catalog_state(failed)
            print('Catalog reload rejected:', error, flush=True)
            return {'ok': False, 'changed': False, 'error': str(error)}
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            summary = catalog.sync(c, seed, link_fields, publish_existing=publish_existing)
        applied = {'stamp': stamp, 'summary': summary, 'appliedAt': time.time(),
                   'source': str(overlay or base), 'overlay': bool(overlay),
                   'hash': hashlib.sha256(json.dumps(seed, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]}
        write_catalog_state(applied)
        return {'ok': True, 'changed': True, 'summary': summary, 'state': applied}


def maybe_apply_catalog():
    """Cheap change check used by read endpoints; the file watcher of last resort."""
    now = time.monotonic()
    if now - CATALOG_WATCH['at'] < 1.0:
        return
    CATALOG_WATCH['at'] = now
    try:
        result = apply_catalog()
        if result.get('changed'):
            print('Catalog hot reload applied:', json.dumps(result['summary'], ensure_ascii=False), flush=True)
    except (OSError, sqlite3.Error, ValueError) as error:
        print('Catalog hot reload failed:', error, flush=True)


def catalog_status():
    base, overlay = catalog_files()
    stamp = catalog_stamp(base, overlay)
    state = read_catalog_state()
    return {
        'bundledPath': str(base),
        'overlayPath': str(DATA / 'seed.json'),
        'overlay': str(overlay) if overlay else None,
        'source': state.get('source'),
        'appliedAt': state.get('appliedAt'),
        'summary': state.get('summary'),
        'total': (state.get('summary') or {}).get('total'),
        'pending': state.get('stamp') != stamp,
        'error': state.get('error'),
        'errorAt': state.get('errorAt'),
    }


def validate_overlay(document):
    """Dry-run an overlay against the bundled catalog before it is stored."""
    catalog.validate_seed(document)
    base, _ = catalog_files()
    bundled = json.loads(base.read_text(encoding='utf-8'))
    return catalog.merge(bundled, document)


def store_overlay(body):
    target = DATA / 'seed.json'
    if target.is_symlink():
        raise APIError(400, 'data/seed.json 不能是符号链接')
    if target.is_file():
        backup = DATA / 'seed.json.bak'
        if backup.is_symlink():
            raise APIError(400, '目录备份不能是符号链接')
        shutil.copyfile(target, backup)
        os.chmod(backup, 0o600)
    temporary = DATA / ('.seed-' + secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists() and temporary.parent == DATA:
            temporary.unlink()


def remove_overlay():
    target = DATA / 'seed.json'
    if target.is_symlink():
        raise APIError(400, 'data/seed.json 不能是符号链接')
    if target.is_file():
        backup = DATA / 'seed.json.bak'
        if backup.is_symlink():
            raise APIError(400, '目录备份不能是符号链接')
        os.replace(target, backup)
        os.chmod(backup, 0o600)


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA, 0o700)
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY, name TEXT NOT NULL, rank INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS links(id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL REFERENCES categories(id), name TEXT NOT NULL, url TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', icon TEXT NOT NULL DEFAULT 'link', visibility TEXT NOT NULL DEFAULT 'private', pinned INTEGER NOT NULL DEFAULT 0, rank INTEGER NOT NULL DEFAULT 0, aliases TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admin(id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL, display TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS favorites(account_id INTEGER NOT NULL REFERENCES accounts(id), link_id INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE, PRIMARY KEY(account_id,link_id));
        CREATE TABLE IF NOT EXISTS prefs(account_id INTEGER PRIMARY KEY REFERENCES accounts(id), music_autoplay INTEGER, volume REAL, muted INTEGER);
        CREATE TABLE IF NOT EXISTS catalog_state(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
        ''')
        try:
            c.execute('ALTER TABLE sessions ADD COLUMN account_id INTEGER NOT NULL DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        if 'icon_url' not in {r[1] for r in c.execute('PRAGMA table_info(links)')}:
            c.execute("ALTER TABLE links ADD COLUMN icon_url TEXT NOT NULL DEFAULT ''")
        catalog.ensure_schema(c)
        if not c.execute('SELECT 1 FROM settings').fetchone():
            seed = load_catalog()
            if seed['settings'].get('musicUrl') == '/media/music.wav':
                media = DATA / 'media'
                media.mkdir(exist_ok=True)
                if not (media / 'music.wav').exists():
                    shutil.copyfile(ROOT / 'ambient.wav', media / 'music.wav')
                    os.chmod(media / 'music.wav', 0o600)
            c.execute('INSERT INTO settings VALUES(1,?)', (json.dumps(seed['settings'], ensure_ascii=False),))
            for cat in seed['categories']:
                c.execute('INSERT INTO categories(id,name,rank,catalog_key) VALUES(?,?,?,?)', (cat['id'],cat['name'],cat['rank'],cat['catalog_key']))
                catalog.remember(c, 'category', cat['catalog_key'])
            for item in seed['links']:
                v = link_fields(item, c)
                v['catalog_key'] = item['catalog_key']
                c.execute('INSERT INTO links('+','.join(v)+') VALUES('+','.join('?' for _ in v)+')', tuple(v.values()))
                catalog.remember(c, 'link', item['catalog_key'])
        if not c.execute('SELECT 1 FROM admin').fetchone():
            secret_file = DATA / 'initial-admin-password'
            # Reuse a pending bootstrap secret if startup was interrupted before DB commit.
            if secret_file.is_symlink():
                raise RuntimeError('Initial password file must not be a symbolic link')
            if secret_file.exists():
                password = secret_file.read_text().strip()
                if not 12 <= len(password) <= 256:
                    raise RuntimeError('Pending bootstrap password is invalid')
                os.chmod(secret_file, 0o600)
            else:
                password = secrets.token_urlsafe(18)
                fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as f:
                    f.write(password + '\n')
                    f.flush()
                    os.fsync(f.fileno())
            c.execute('INSERT INTO admin VALUES(1,?,?)', ('astral', password_hash(password)))
            print('Administrator initialized; retrieve the password with the admin-password command.', flush=True)
        # One-time migration of the single-admin row into the accounts model.
        if not c.execute('SELECT 1 FROM accounts').fetchone():
            a = c.execute('SELECT * FROM admin').fetchone()
            if a:
                c.execute('INSERT INTO accounts(id,username,password,display) VALUES(?,?,?,?)', (a['id'], a['username'], a['password'], ''))
        c.execute('DELETE FROM sessions WHERE expires < ?', (int(time.time()),))
    os.chmod(DB, 0o600)

class Handler(BaseHTTPRequestHandler):
    server_version = 'Homehub'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        # Never log credentials, headers, request bodies or query strings.
        print(f'{self.command} {urlsplit(self.path).path[:180]}', flush=True)

    def common_headers(self, content_type, length, cache='no-store'):
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Content-Security-Policy', icon_library.SVG_CSP if content_type=='image/svg+xml' else "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; media-src 'self' https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")

    def reply(self, status, data, headers=None):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.common_headers('application/json; charset=utf-8', len(body))
        for k,v in (headers or {}).items():
            self.send_header(k,v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def body(self, limit=MAX_JSON, raw=False):
        try:
            length = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            length = -1
        if length < 0 or length > limit:
            self.close_connection = True
            raise APIError(413, '请求大小不正确或超出限制')
        if self.headers.get('Transfer-Encoding'):
            raise APIError(400, '不支持此请求传输方式')
        b = self.rfile.read(length)
        if len(b) != length:
            raise APIError(400, '请求不完整')
        if raw:
            return b
        if not self.headers.get('Content-Type', '').lower().startswith('application/json'):
            raise APIError(415, '请使用 JSON 请求')
        try:
            v = json.loads(b)
            if not isinstance(v, dict):
                raise ValueError()
            return v
        except (ValueError, UnicodeError):
            raise APIError(400, 'JSON 格式不正确')

    def session(self, c):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            token = cookie[COOKIE].value if COOKIE in cookie else ''
        except Exception:
            token = ''
        if not token or len(token)>200:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        return c.execute('SELECT s.token_hash,s.csrf,s.account_id,a.username,a.display FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=? AND s.expires>?', (digest, int(time.time()))).fetchone()

    def origin_check(self):
        origin = self.headers.get('Origin')
        if origin and origin != ORIGIN:
            raise APIError(403, '请求来源不允许')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise APIError(403, '不允许跨站请求')

    def require_auth(self, c, mutation=False):
        s = self.session(c)
        if not s:
            raise APIError(401, '请先登录管理员账号')
        if mutation:
            self.origin_check()
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', '').encode('utf-8'), s['csrf'].encode('utf-8')):
                raise APIError(403, '操作凭据已失效，请刷新页面后重试')
        return s

    def cookie_header(self, token, clear=False):
        return f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if clear else SESSION_SECONDS}' + ('; Secure' if COOKIE_SECURE else '')

    def data(self, c):
        s = self.session(c)
        query = 'SELECT * FROM links' + ('' if s else " WHERE visibility='public'") + ' ORDER BY rank,id'
        links = [dict(r) for r in c.execute(query)]
        for item in links:
            item['pinned'] = bool(item['pinned'])
            item['iconUrl'] = resolve_icon(item.get('icon_url') or '', missing_ok=True)
        categories = [dict(r) for r in c.execute('SELECT * FROM categories ORDER BY rank,id')]
        if not s:
            used = {x['category_id'] for x in links}
            categories = [x for x in categories if x['id'] in used]
        settings = json.loads(c.execute('SELECT data FROM settings').fetchone()[0])
        settings.setdefault('musicAutoplay', False)
        me = None
        if s:
            p = c.execute('SELECT music_autoplay,volume,muted FROM prefs WHERE account_id=?', (s['account_id'],)).fetchone()
            me = {'username': s['username'], 'displayName': s['display'] or s['username'],
                  'prefs': {'musicAutoplay': None if not p or p['music_autoplay'] is None else bool(p['music_autoplay']),
                            'volume': (p['volume'] if p and p['volume'] is not None else 0.5),
                            'muted': bool(p['muted']) if p else False}}
            if p and p['music_autoplay'] is not None:
                settings['musicAutoplay'] = bool(p['music_autoplay'])
        favorites = [r['link_id'] for r in c.execute('SELECT link_id FROM favorites WHERE account_id=?', (s['account_id'],))] if s else []
        return {'authenticated':bool(s), 'csrf':s['csrf'] if s else None,
                'username':me['displayName'] if me else None,
                'me':me, 'favorites':favorites,
                'settings':settings,
                'categories':categories, 'links':links,
                'catalog':{'total':c.execute('SELECT count(*) FROM links').fetchone()[0],
                           'visible':len(links),
                           'hidden':c.execute('SELECT count(*) FROM links').fetchone()[0]-len(links)},
                'version':VERSION}

    def do_HEAD(self):
        self.handle_request()
    def do_GET(self):
        self.handle_request()
    def do_POST(self):
        self.handle_request()
    def do_PUT(self):
        self.handle_request()
    def do_DELETE(self):
        self.handle_request()

    def handle_request(self):
        try:
            self.connection.settimeout(30)
            path = urlsplit(self.path).path
            if path.startswith('/api/'):
                if path in ('/api/data','/api/me','/api/export','/api/catalog') and self.command in ('GET','HEAD'):
                    maybe_apply_catalog()
                with db() as c:
                    self.api(path, c)
            elif self.command in ('GET', 'HEAD'):
                self.static(path)
            else:
                raise APIError(405, '不支持此操作')
        except APIError as e:
            self.close_connection = True
            self.reply(e.status, {'error':e.message}, {'Connection':'close'})
        except icon_library.IconBusy as e:
            self.close_connection = True
            self.reply(429, {'error':str(e)}, {'Connection':'close'})
        except icon_library.IconError as e:
            self.close_connection = True
            self.reply(400, {'error':str(e)}, {'Connection':'close'})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception as e:
            print('Request failed:', type(e).__name__, flush=True)
            self.close_connection = True
            self.reply(500, {'error':'服务暂时无法完成操作，请重试'})

    def api(self, path, c):
        method = self.command
        if path in ('/api/data','/api/me') and method in ('GET','HEAD'):
            return self.reply(200, self.data(c))
        if path == '/api/icons' and method == 'GET':
            self.require_auth(c)
            return self.reply(200, {'library':icon_library.list_icons(DATA)})
        if path == '/api/catalog' and method in ('GET','HEAD'):
            self.require_auth(c)
            return self.reply(200, catalog_status())
        if path == '/api/health' and method in ('GET','HEAD'):
            c.execute('SELECT 1')
            return self.reply(200, {'ok':True, 'version':VERSION})
        if path == '/api/login' and method == 'POST':
            self.origin_check()
            v = self.body()
            username = text(v.get('username', ''), '用户名', 80, True)
            password = text(v.get('password', ''), '密码', 256, True)
            ip = self.headers.get('X-Real-IP', self.client_address[0])[:80]
            now = time.time()
            # Serialize login verification to bound scrypt memory/CPU and rate-limit concurrent attacks.
            with AUTH_LOCK:
                for key in list(ATTEMPTS):
                    if ATTEMPTS[key][0] < now-900:
                        del ATTEMPTS[key]
                start, count = ATTEMPTS.get(ip, (now, 0))
                if count >= 10:
                    raise APIError(429, '尝试次数过多，请 15 分钟后再试')
                ATTEMPTS[ip] = (start, count+1)
                a = c.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
                good = a is not None and check_password(password, a['password'])
                if not good or not hmac.compare_digest(username.encode('utf-8'), a['username'].encode('utf-8')):
                    raise APIError(401, '用户名或密码不正确')
                ATTEMPTS.pop(ip, None)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            c.execute('DELETE FROM sessions WHERE expires < ?', (int(now),))
            c.execute('INSERT INTO sessions(token_hash,csrf,expires,account_id) VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), csrf, int(now)+SESSION_SECONDS, a['id']))
            c.commit()
            return self.reply(200, {'ok':True,'csrf':csrf,'username':a['username']}, {'Set-Cookie':self.cookie_header(token)})
        if path == '/api/export' and method == 'GET':
            self.require_auth(c)
            d = self.data(c)
            return self.reply(200, {'version':1, 'catalogRegistry':catalog.registry(c), **{k:d[k] for k in ('settings','categories','links')}}, {'Content-Disposition':'attachment; filename="homehub-navigation.json"'})
        if method not in ('POST','PUT','DELETE'):
            raise APIError(404, '接口不存在')
        s = self.require_auth(c, mutation=True)
        if path == '/api/catalog' and method == 'POST':
            result = apply_catalog(force=True)
            if not result.get('ok'):
                raise APIError(400, result.get('error') or '目录数据无效')
            return self.reply(200, {'ok':True, 'summary':result.get('summary'), 'status':catalog_status()})
        if path == '/api/catalog/seed' and method == 'POST':
            body = self.body(CATALOG_SEED_LIMIT, raw=True)
            try:
                document = json.loads(body.decode('utf-8'))
                validate_overlay(document)
            except (UnicodeError, ValueError) as error:
                raise APIError(400, f'目录数据无效：{error}')
            store_overlay(body)
            result = apply_catalog(force=True)
            if not result.get('ok'):
                raise APIError(400, result.get('error') or '目录数据无效')
            return self.reply(200, {'ok':True, 'summary':result.get('summary'), 'status':catalog_status()})
        if path == '/api/catalog/seed' and method == 'DELETE':
            remove_overlay()
            result = apply_catalog(force=True)
            if not result.get('ok'):
                raise APIError(400, result.get('error') or '目录数据无效')
            return self.reply(200, {'ok':True, 'summary':result.get('summary'), 'status':catalog_status()})
        if path == '/api/logout' and method == 'POST':
            if self.headers.get('Content-Length') not in (None, '0'):
                self.body()
            c.execute('DELETE FROM sessions WHERE token_hash=?', (s['token_hash'],))
            c.commit()
            return self.reply(200, {'ok':True}, {'Set-Cookie':self.cookie_header('', clear=True)})
        if path == '/api/music' and method == 'POST':
            filename = text(unquote(self.headers.get('X-Filename', '')), '音频文件名', 255, True)
            # Client filenames are labels only, never paths on the server.
            basename = Path(filename.replace('\\', '/')).name
            ext = Path(basename).suffix.lower()
            music_title = ''.join(ch for ch in Path(basename).stem
                                  if not unicodedata.category(ch).startswith('C')).strip()[:100] or '背景音乐'
            if ext not in ('.mp3','.ogg','.wav','.m4a','.opus'):
                raise APIError(400, '支持 MP3、OGG、WAV、M4A 和 OPUS 音频')
            b = self.body(MAX_AUDIO, raw=True)
            if len(b)<16:
                raise APIError(400, '音频文件为空或无效')
            valid = (ext == '.mp3' and (b.startswith(b'ID3') or (b[0]==255 and b[1]&224==224))) or (ext in ('.ogg','.opus') and b.startswith(b'OggS')) or (ext=='.wav' and b.startswith(b'RIFF') and b[8:12]==b'WAVE') or (ext=='.m4a' and b[4:8]==b'ftyp')
            if not valid:
                raise APIError(400, '文件内容与音频格式不匹配')
            music_url = '/media/music' + ext + '?v=' + hashlib.sha256(b).hexdigest()[:16]
            # Serialize upload defaults with other SQLite writes, reading settings only
            # after the write lock so unrelated site/autoplay fields remain current.
            c.execute('BEGIN IMMEDIATE')
            old = json.loads(c.execute('SELECT data FROM settings').fetchone()[0])
            old['musicUrl'] = music_url
            old['musicTitle'] = music_title
            media = DATA / 'media'
            if media.is_symlink():
                raise APIError(400, '音频目录不能为符号链接')
            media.mkdir(exist_ok=True)
            tmp = media / ('.upload-'+secrets.token_hex(8))
            created = False
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(b)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, media / ('music'+ext))
                c.execute('UPDATE settings SET data=? WHERE id=1', (json.dumps(old,ensure_ascii=False),))
                c.commit()
            finally:
                if created:
                    tmp.unlink(missing_ok=True)
            return self.reply(200, {'musicUrl':music_url, 'musicTitle':music_title})
        if path == '/api/icons' and method == 'POST':
            with icon_library.operation():
                # Authentication and Origin/CSRF validation occurred before reading bytes.
                ctype=self.headers.get('Content-Type','').lower()
                if ctype.startswith('application/json'):
                    target=self.body().get('url')
                    b=fetch_remote_icon(target)
                else:
                    filename=text(unquote(self.headers.get('X-Filename','')),'图标文件名',255,True)
                    ext=Path(filename.replace('\\','/')).suffix.lower()
                    if ext not in icon_library.MIME:
                        raise APIError(400,'仅支持 PNG、JPG、WebP、SVG、ICO 图标')
                    b=self.body(icon_library.MAX_BYTES,raw=True)
                    actual=icon_library.validate_image(b)
                    if actual != ('.jpg' if ext=='.jpeg' else ext):
                        raise APIError(400,'图标文件内容与扩展名不一致')
                name=save_icon_bytes(b)
                return self.reply(200,{'ok':True,'icon':name,'url':'/media/icons/'+name})
        if path == '/api/settings' and method == 'PUT':
            old = json.loads(c.execute('SELECT data FROM settings').fetchone()[0])
            new = settings_fields(self.body(), old)
            c.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(new,ensure_ascii=False),))
            c.commit()
            return self.reply(200, {'ok':True})
        if path == '/api/password' and method == 'POST':
            v=self.body()
            old=text(v.get('currentPassword',''),'当前密码',256,True)
            new=text(v.get('newPassword',''),'新密码',256,True)
            if len(new)<12:
                raise APIError(400,'新密码至少需要 12 个字符')
            with AUTH_LOCK:
                a=c.execute('SELECT password FROM accounts WHERE id=?',(s['account_id'],)).fetchone()
                if not check_password(old,a['password']):
                    raise APIError(400,'当前密码不正确')
                c.execute('UPDATE accounts SET password=? WHERE id=?',(password_hash(new),s['account_id']))
                c.execute('DELETE FROM sessions WHERE account_id=? AND token_hash<>?',(s['account_id'],s['token_hash']))
                c.commit()
            if s['account_id']==1:
                (DATA/'initial-admin-password').unlink(missing_ok=True)
            return self.reply(200,{'ok':True})
        if path == '/api/profile' and method == 'PUT':
            v=self.body()
            display=text(v.get('displayName',''),'显示名称',40)
            c.execute('UPDATE accounts SET display=? WHERE id=?',(display,s['account_id']))
            c.commit()
            return self.reply(200,{'ok':True,'displayName':display or s['username']})
        if path == '/api/favorites' and method == 'PUT':
            v=self.body()
            link_id=v.get('linkId');on=v.get('on')
            if not isinstance(link_id,int) or not isinstance(on,bool):
                raise APIError(400,'收藏参数格式不正确')
            if not c.execute('SELECT 1 FROM links WHERE id=?',(link_id,)).fetchone():
                raise APIError(404,'入口不存在')
            if on:
                c.execute('INSERT OR IGNORE INTO favorites(account_id,link_id) VALUES(?,?)',(s['account_id'],link_id))
            else:
                c.execute('DELETE FROM favorites WHERE account_id=? AND link_id=?',(s['account_id'],link_id))
            c.commit()
            return self.reply(200,{'ok':True,'on':on})
        if path == '/api/prefs' and method == 'PUT':
            v=self.body()
            p=c.execute('SELECT music_autoplay,volume,muted FROM prefs WHERE account_id=?',(s['account_id'],)).fetchone()
            cur=[None if not p or p['music_autoplay'] is None else bool(p['music_autoplay']),
                 float(p['volume']) if p and p['volume'] is not None else 0.5,
                 bool(p['muted']) if p else False]
            if 'musicAutoplay' in v:
                ma=v['musicAutoplay']
                if ma is not None and not isinstance(ma,bool):raise APIError(400,'自动播放偏好格式不正确')
                cur[0]=ma
            if 'volume' in v:
                vol=v['volume']
                if not isinstance(vol,(int,float)) or not 0<=vol<=1:raise APIError(400,'音量格式不正确')
                cur[1]=float(vol)
            if 'muted' in v:
                if not isinstance(v['muted'],bool):raise APIError(400,'静音偏好格式不正确')
                cur[2]=v['muted']
            c.execute('INSERT INTO prefs(account_id,music_autoplay,volume,muted) VALUES(?,?,?,?) '+
                      'ON CONFLICT(account_id) DO UPDATE SET music_autoplay=excluded.music_autoplay,volume=excluded.volume,muted=excluded.muted',
                      (s['account_id'],None if cur[0] is None else int(cur[0]),cur[1],int(cur[2])))
            c.commit()
            return self.reply(200,{'ok':True,'prefs':{'musicAutoplay':cur[0],'volume':cur[1],'muted':cur[2]}})
        if path == '/api/import' and method == 'POST':
            v=self.body(MAX_BACKUP)
            cats, links=v.get('categories'),v.get('links')
            if v.get('version')!=1 or not isinstance(cats,list) or not isinstance(links,list) or not 0<=len(cats)<=100 or len(links)>2000:
                raise APIError(400,'备份格式不正确，最多支持 100 个分类 / 2000 个入口')
            clean_cats=[];seen=set();category_keys=set()
            for cat in cats:
                if not isinstance(cat,dict) or type(cat.get('id')) is not int or cat['id']<=0 or cat['id'] in seen:
                    raise APIError(400,'备份分类编号不正确')
                key=imported_key(cat.get('catalog_key'))
                if key is not None and key in category_keys:
                    raise APIError(400,'备份分类目录标识重复')
                if key is not None:category_keys.add(key)
                clean_cats.append({'id':cat['id'],**category_fields(cat),'catalog_key':key});seen.add(cat['id'])
            records=v.get('catalogRegistry')
            if records is not None:
                if not isinstance(records,list) or len(records)>10000:
                    raise APIError(400,'目录历史格式不正确')
                for record in records:
                    if not isinstance(record,dict) or record.get('kind') not in ('category','link') or not catalog.valid_key(record.get('key')):
                        raise APIError(400,'目录历史格式不正确')
            old=json.loads(c.execute('SELECT data FROM settings').fetchone()[0])
            # Imports restore the source backup's behavior: pre-autoplay backups
            # default false, unlike legacy PUT requests which preserve live values.
            newsettings=settings_fields(v.get('settings',{}),dict(old,musicAutoplay=False))
            # Transaction rolls back in handle_request if any entry fails validation.
            c.execute('DELETE FROM links');c.execute('DELETE FROM categories');c.execute('DELETE FROM favorites')
            if records is not None:
                c.execute('DELETE FROM catalog_registry')
                for record in records:catalog.remember(c,record['kind'],record['key'])
            for cat in clean_cats:
                c.execute('INSERT INTO categories(id,name,rank,catalog_key) VALUES(?,?,?,?)',tuple(cat.values()))
                if cat['catalog_key']:catalog.remember(c,'category',cat['catalog_key'])
            link_keys=set(); missing_icons=[]
            for item in links:
                if not isinstance(item,dict):raise APIError(400,'备份入口格式不正确')
                clean=link_fields(item,c)
                key=imported_key(item.get('catalog_key'))
                if key is not None and key in link_keys:
                    raise APIError(400,'备份入口目录标识重复')
                clean['catalog_key']=key
                reference=item.get('iconUrl', item.get('icon_url', ''))
                clean['icon_url']=resolve_icon(reference,missing_ok=True)
                if reference and not clean['icon_url']:
                    missing_icons.append(item.get('id',item.get('name','')))
                if key is not None:
                    link_keys.add(key);catalog.remember(c,'link',key)
                c.execute('INSERT INTO links('+','.join(clean)+') VALUES('+','.join('?' for _ in clean)+')',tuple(clean.values()))
            c.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(newsettings,ensure_ascii=False),));c.commit()
            return self.reply(200,{'ok':True,'missingIcons':missing_icons})
        m=re.fullmatch(r'/api/(links|categories)(?:/(\d+))?',path)
        if not m:
            raise APIError(404,'接口不存在')
        table, ident=m.group(1),m.group(2)
        if method=='POST' and ident is None:
            limit=2000 if table=='links' else 100
            if c.execute(f'SELECT count(*) FROM {table}').fetchone()[0]>=limit:
                raise APIError(400,'已达到入口或分类数量上限')
            v=self.body()
            clean=link_fields(v,c) if table=='links' else category_fields(v)
            cur=c.execute(f'INSERT INTO {table}('+','.join(clean)+') VALUES('+','.join('?' for _ in clean)+')',tuple(clean.values()))
            if table=='links' and 'iconUrl' in v:
                c.execute('UPDATE links SET icon_url=? WHERE id=?',(resolve_icon(v['iconUrl']),cur.lastrowid))
            c.commit();return self.reply(201,{'ok':True,'id':cur.lastrowid})
        if ident is None or not c.execute(f'SELECT 1 FROM {table} WHERE id=?',(int(ident),)).fetchone():
            raise APIError(404,'入口或分类不存在')
        if method=='PUT':
            v=self.body()
            if table=='links' and 'launch_mode' not in v:
                v['launch_mode']=c.execute('SELECT launch_mode FROM links WHERE id=?',(int(ident),)).fetchone()[0]
            clean=link_fields(v,c) if table=='links' else category_fields(v)
            c.execute(f'UPDATE {table} SET '+','.join(k+'=?' for k in clean)+' WHERE id=?',(*clean.values(),int(ident)))
            if table=='links' and 'iconUrl' in v:
                c.execute('UPDATE links SET icon_url=? WHERE id=?',(resolve_icon(v['iconUrl']),int(ident)))
        elif method=='DELETE':
            if table=='categories' and c.execute('SELECT 1 FROM links WHERE category_id=?',(int(ident),)).fetchone():
                raise APIError(409,'分类内仍有入口，请先移动或删除这些入口')
            c.execute(f'DELETE FROM {table} WHERE id=?',(int(ident),))
        else:
            raise APIError(405,'不支持此操作')
        c.commit();return self.reply(200,{'ok':True})

    def static(self,path):
        if path in ('/','/nav','/admin','/nav/','/admin/'):
            p=STATIC/'index.html'
            content_type='text/html; charset=utf-8'
        elif path.startswith('/assets/'):
            tail=unquote(path[len('/assets/'):])
            p=(STATIC/tail).resolve()
            if not p.is_relative_to(STATIC.resolve()):
                raise APIError(404,'文件不存在')
            content_type=mimetypes.guess_type(p)[0] or 'application/octet-stream'
        elif re.fullmatch(r'/media/icons/[A-Za-z0-9_-]+\.(png|jpe?g|webp|svg|ico)',path):
            try:
                p=icon_library.safe_file(icon_library_dir(),path.rsplit('/',1)[1])
            except icon_library.IconError:
                raise APIError(404,'文件不存在')
            content_type=icon_library.MIME[p.suffix]
        elif re.fullmatch(r'/media/music\.(mp3|ogg|wav|m4a|opus)',path):
            p=DATA/path.lstrip('/')
            content_type={'.mp3':'audio/mpeg','.ogg':'audio/ogg','.wav':'audio/wav','.m4a':'audio/mp4','.opus':'audio/ogg'}[p.suffix]
        elif path=='/robots.txt':
            b=b'User-agent: *\nDisallow: /admin\nDisallow: /api/\n'
            self.send_response(200);self.common_headers('text/plain',len(b));self.end_headers()
            if self.command!='HEAD':self.wfile.write(b)
            return
        elif path=='/favicon.ico':
            p=STATIC/'favicon.ico'
            content_type='image/x-icon'
        else:
            raise APIError(404,'页面不存在')
        if not p.is_file():raise APIError(404,'文件不存在')
        if p.name=='index.html':
            b=p.read_bytes().replace(b'__HOMEHUB_ASSET_VERSION__',ASSET_VERSION.encode('ascii'))
            self.send_response(200)
            self.common_headers(content_type,len(b),'no-cache')
            self.end_headers()
            if self.command!='HEAD':self.wfile.write(b)
            return
        size=p.stat().st_size
        start,end=0,size-1
        range_header=self.headers.get('Range','') if path.startswith('/media/') else ''
        status=200
        if range_header:
            m=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
            if not m or not any(m.groups()):raise APIError(416,'范围不正确')
            if m[1]:
                start=int(m[1]);end=min(int(m[2]),end) if m[2] else end
            else:start=max(0,size-int(m[2]))
            if start>end or start>=size:raise APIError(416,'范围不正确')
            status=206
        self.send_response(status)
        self.common_headers(content_type,end-start+1,'no-cache' if p.name=='index.html' else 'public, max-age=300')
        if path.startswith('/media/'):
            self.send_header('Accept-Ranges','bytes')
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command!='HEAD':
            with p.open('rb') as f:
                f.seek(start);remaining=end-start+1
                while remaining:
                    chunk=f.read(min(65536,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)

class Server(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=64

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'serve'
    if cmd=='sync-catalog':
        if not DB.is_file():
            raise SystemExit('Catalog sync requires an initialized database; start the service first.')
        if any(x!='--publish-existing' for x in sys.argv[2:]):
            raise SystemExit('Usage: app.py sync-catalog [--publish-existing]')
        seed=load_catalog()
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            result=catalog.sync(c,seed,link_fields,publish_existing='--publish-existing' in sys.argv[2:])
        print(json.dumps(result,ensure_ascii=False))
        raise SystemExit(0)
    init()
    if cmd=='admin-password':
        p=DATA/'initial-admin-password'
        if p.exists():print(p.read_text().strip())
        else:print('初始密码已失效；如忘记密码，请运行 reset-password。')
    elif cmd=='reset-password':
        password=secrets.token_urlsafe(18)
        with db() as c:
            c.execute('UPDATE accounts SET password=? WHERE id=1',(password_hash(password),))
            c.execute('DELETE FROM sessions WHERE account_id=1');c.commit()
        p=DATA/'initial-admin-password';p.write_text(password+'\n');os.chmod(p,0o600)
        print('管理员 astral 的新密码：'+password)
    elif cmd=='add-account':
        try:
            username,password=sys.argv[2],sys.argv[3]
        except IndexError:
            raise SystemExit('Usage: app.py add-account USERNAME PASSWORD')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{2,40}',username):
            raise SystemExit('用户名仅限 2-40 位字母、数字、点、下划线、连字符')
        if not 8<=len(password)<=256:
            raise SystemExit('密码至少 8 位')
        with db() as c:
            if c.execute('SELECT 1 FROM accounts WHERE username=?',(username,)).fetchone():
                raise SystemExit('用户名已存在')
            c.execute('INSERT INTO accounts(username,password,display) VALUES(?,?,?)',(username,password_hash(password),''))
            c.commit()
        print(f'账户 {username} 已创建，权限与管理员一致。')
    elif cmd=='serve':
        print('Homehub listening on :8080',flush=True)
        Server((os.environ.get('HOMEHUB_BIND','0.0.0.0'),int(os.environ.get('HOMEHUB_PORT','8080'))),Handler).serve_forever()
    else:raise SystemExit('Unknown command')
