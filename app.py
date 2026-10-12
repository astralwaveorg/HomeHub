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
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('HOMEHUB_DATA', '/data'))
STATIC = ROOT / 'static'
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
    if local_media and re.fullmatch(r'/media/music\.(mp3|ogg|wav|m4a)', value):
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
    pinned = v.get('pinned', False)
    if not isinstance(pinned, bool):
        raise APIError(400, '常用标记必须为布尔值')
    return {'category_id':category_id, 'name':text(v.get('name', ''), '名称', 80, True),
            'url':url(v.get('url', '')), 'description':text(v.get('description', ''), '描述', 200),
            'icon':text(v.get('icon', 'link'), '图标', 40), 'visibility':visibility,
            'pinned':int(pinned), 'rank':rank(v.get('rank', 0)), 'aliases':text(v.get('aliases', ''), '搜索别名', 200)}

def settings_fields(v, old):
    if not isinstance(v, dict):
        raise APIError(400, '设置格式不正确')
    out = dict(old)
    for key, limit in [('title', 40), ('tagline', 100), ('subtitle', 200), ('musicTitle', 100)]:
        if key in v:
            out[key] = text(v[key], key, limit, key == 'title')
    if 'musicUrl' in v:
        out['musicUrl'] = url(v['musicUrl'], allow_empty=True, local_media=True)
    return out

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
        ''')
        if not c.execute('SELECT 1 FROM settings').fetchone():
            seed = json.loads((ROOT / 'seed.json').read_text())
            if seed['settings'].get('musicUrl') == '/media/music.wav':
                media = DATA / 'media'
                media.mkdir(exist_ok=True)
                if not (media / 'music.wav').exists():
                    shutil.copyfile(ROOT / 'ambient.wav', media / 'music.wav')
                    os.chmod(media / 'music.wav', 0o600)
            c.execute('INSERT INTO settings VALUES(1,?)', (json.dumps(seed['settings'], ensure_ascii=False),))
            for cat in seed['categories']:
                c.execute('INSERT INTO categories(id,name,rank) VALUES(?,?,?)', (cat['id'],cat['name'],cat['rank']))
            for item in seed['links']:
                v = link_fields(item, c)
                c.execute('INSERT INTO links('+','.join(v)+') VALUES('+','.join('?' for _ in v)+')', tuple(v.values()))
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
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; media-src 'self' https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")

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
        return c.execute('SELECT token_hash,csrf FROM sessions WHERE token_hash=? AND expires>?', (digest, int(time.time()))).fetchone()

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
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), s['csrf']):
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
        categories = [dict(r) for r in c.execute('SELECT * FROM categories ORDER BY rank,id')]
        if not s:
            used = {x['category_id'] for x in links}
            categories = [x for x in categories if x['id'] in used]
        return {'authenticated':bool(s), 'csrf':s['csrf'] if s else None,
                'username':c.execute('SELECT username FROM admin').fetchone()[0] if s else None,
                'settings':json.loads(c.execute('SELECT data FROM settings').fetchone()[0]),
                'categories':categories, 'links':links}

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
                with db() as c:
                    self.api(path, c)
            elif self.command in ('GET', 'HEAD'):
                self.static(path)
            else:
                raise APIError(405, '不支持此操作')
        except APIError as e:
            self.close_connection = True
            self.reply(e.status, {'error':e.message}, {'Connection':'close'})
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
        if path == '/api/health' and method in ('GET','HEAD'):
            c.execute('SELECT 1')
            return self.reply(200, {'ok':True})
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
                a = c.execute('SELECT * FROM admin').fetchone()
                good = check_password(password, a['password'])
                if not good or not hmac.compare_digest(username, a['username']):
                    raise APIError(401, '用户名或密码不正确')
                ATTEMPTS.pop(ip, None)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            c.execute('DELETE FROM sessions WHERE expires < ?', (int(now),))
            c.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), csrf, int(now)+SESSION_SECONDS))
            c.commit()
            return self.reply(200, {'ok':True,'csrf':csrf,'username':a['username']}, {'Set-Cookie':self.cookie_header(token)})
        if path == '/api/export' and method == 'GET':
            self.require_auth(c)
            d = self.data(c)
            return self.reply(200, {'version':1, **{k:d[k] for k in ('settings','categories','links')}}, {'Content-Disposition':'attachment; filename="homehub-navigation.json"'})
        if method not in ('POST','PUT','DELETE'):
            raise APIError(404, '接口不存在')
        s = self.require_auth(c, mutation=True)
        if path == '/api/logout' and method == 'POST':
            if self.headers.get('Content-Length') not in (None, '0'):
                self.body()
            c.execute('DELETE FROM sessions WHERE token_hash=?', (s['token_hash'],))
            c.commit()
            return self.reply(200, {'ok':True}, {'Set-Cookie':self.cookie_header('', clear=True)})
        if path == '/api/music' and method == 'POST':
            filename = text(unquote(self.headers.get('X-Filename', '')), '音频文件名', 255, True)
            ext = Path(filename).suffix.lower()
            if ext not in ('.mp3','.ogg','.wav','.m4a'):
                raise APIError(400, '支持 MP3、OGG、WAV 和 M4A 音频')
            b = self.body(MAX_AUDIO, raw=True)
            if len(b)<16:
                raise APIError(400, '音频文件为空或无效')
            valid = (ext == '.mp3' and (b.startswith(b'ID3') or (b[0]==255 and b[1]&224==224))) or (ext=='.ogg' and b.startswith(b'OggS')) or (ext=='.wav' and b.startswith(b'RIFF') and b[8:12]==b'WAVE') or (ext=='.m4a' and b[4:8]==b'ftyp')
            if not valid:
                raise APIError(400, '文件内容与音频格式不匹配')
            media = DATA / 'media'
            media.mkdir(exist_ok=True)
            tmp = media / ('.upload-'+secrets.token_hex(8))
            tmp.write_bytes(b)
            os.chmod(tmp,0o600)
            tmp.replace(media / ('music'+ext))
            old = json.loads(c.execute('SELECT data FROM settings').fetchone()[0])
            old['musicUrl'] = '/media/music'+ext
            c.execute('UPDATE settings SET data=? WHERE id=1', (json.dumps(old,ensure_ascii=False),))
            c.commit()
            return self.reply(200, {'musicUrl':old['musicUrl']})
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
                a=c.execute('SELECT password FROM admin').fetchone()[0]
                if not check_password(old,a):
                    raise APIError(400,'当前密码不正确')
                c.execute('UPDATE admin SET password=? WHERE id=1',(password_hash(new),))
                c.execute('DELETE FROM sessions WHERE token_hash<>?',(s['token_hash'],))
                c.commit()
            (DATA/'initial-admin-password').unlink(missing_ok=True)
            return self.reply(200,{'ok':True})
        if path == '/api/import' and method == 'POST':
            v=self.body(MAX_BACKUP)
            cats, links=v.get('categories'),v.get('links')
            if v.get('version')!=1 or not isinstance(cats,list) or not isinstance(links,list) or not 0<=len(cats)<=100 or len(links)>2000:
                raise APIError(400,'备份格式不正确，最多支持 100 个分类 / 2000 个入口')
            clean_cats=[];seen=set()
            for cat in cats:
                if not isinstance(cat,dict) or type(cat.get('id')) is not int or cat['id']<=0 or cat['id'] in seen:
                    raise APIError(400,'备份分类编号不正确')
                clean_cats.append({'id':cat['id'],**category_fields(cat)});seen.add(cat['id'])
            old=json.loads(c.execute('SELECT data FROM settings').fetchone()[0])
            newsettings=settings_fields(v.get('settings',{}),old)
            # Transaction rolls back in handle_request if any entry fails validation.
            c.execute('DELETE FROM links');c.execute('DELETE FROM categories')
            for cat in clean_cats:
                c.execute('INSERT INTO categories(id,name,rank) VALUES(?,?,?)',tuple(cat.values()))
            for item in links:
                if not isinstance(item,dict):raise APIError(400,'备份入口格式不正确')
                clean=link_fields(item,c)
                c.execute('INSERT INTO links('+','.join(clean)+') VALUES('+','.join('?' for _ in clean)+')',tuple(clean.values()))
            c.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(newsettings,ensure_ascii=False),));c.commit()
            return self.reply(200,{'ok':True})
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
            c.commit();return self.reply(201,{'ok':True,'id':cur.lastrowid})
        if ident is None or not c.execute(f'SELECT 1 FROM {table} WHERE id=?',(int(ident),)).fetchone():
            raise APIError(404,'入口或分类不存在')
        if method=='PUT':
            v=self.body()
            clean=link_fields(v,c) if table=='links' else category_fields(v)
            c.execute(f'UPDATE {table} SET '+','.join(k+'=?' for k in clean)+' WHERE id=?',(*clean.values(),int(ident)))
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
        elif re.fullmatch(r'/media/music\.(mp3|ogg|wav|m4a)',path):
            p=DATA/path.lstrip('/')
            content_type={'.mp3':'audio/mpeg','.ogg':'audio/ogg','.wav':'audio/wav','.m4a':'audio/mp4'}[p.suffix]
        elif path=='/robots.txt':
            b=b'User-agent: *\nDisallow: /admin\nDisallow: /api/\n'
            self.send_response(200);self.common_headers('text/plain',len(b));self.end_headers()
            if self.command!='HEAD':self.wfile.write(b)
            return
        elif path=='/favicon.ico':
            self.send_response(204);self.common_headers('image/x-icon',0);self.end_headers();return
        else:
            raise APIError(404,'页面不存在')
        if not p.is_file():raise APIError(404,'文件不存在')
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
    init()
    cmd=sys.argv[1] if len(sys.argv)>1 else 'serve'
    if cmd=='admin-password':
        p=DATA/'initial-admin-password'
        if p.exists():print(p.read_text().strip())
        else:print('初始密码已失效；如忘记密码，请运行 reset-password。')
    elif cmd=='reset-password':
        password=secrets.token_urlsafe(18)
        with db() as c:
            c.execute('UPDATE admin SET password=? WHERE id=1',(password_hash(password),));c.execute('DELETE FROM sessions');c.commit()
        p=DATA/'initial-admin-password';p.write_text(password+'\n');os.chmod(p,0o600)
        print('管理员 astral 的新密码：'+password)
    elif cmd=='serve':
        print('Homehub listening on :8080',flush=True)
        Server((os.environ.get('HOMEHUB_BIND','0.0.0.0'),int(os.environ.get('HOMEHUB_PORT','8080'))),Handler).serve_forever()
    else:raise SystemExit('Unknown command')
