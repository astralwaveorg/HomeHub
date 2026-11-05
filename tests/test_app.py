"""Run locally: python3 -m unittest discover -s homehub/tests -v.
All credentials and database files are generated only in a temporary directory.
"""
import hashlib
import http.client
import importlib.util
import json
import os
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SEED = json.loads((ROOT/'seed.json').read_text())

class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='homehub-tests-')
        os.environ['HOMEHUB_DATA'] = cls.temp.name
        os.environ['HOMEHUB_ORIGIN'] = 'https://homehub.stream:8443'
        os.environ['HOMEHUB_COOKIE_SECURE'] = 'true'
        spec = importlib.util.spec_from_file_location('homehub_under_test', ROOT/'app.py')
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)
        cls.app.init()
        cls.password = (Path(cls.temp.name)/'initial-admin-password').read_text().strip()
        cls.server = cls.app.Server(('127.0.0.1',0), cls.app.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, method, path, data=None, cookie=None, csrf=None, origin='https://homehub.stream:8443', headers=None, raw=False):
        h = {'Origin':origin} if origin else {}
        if cookie:h['Cookie']=cookie
        if csrf:h['X-CSRF-Token']=csrf
        h.update(headers or {})
        b = data if raw else (json.dumps(data).encode() if data is not None else None)
        if b is not None and not raw:h['Content-Type']='application/json'
        conn=http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request(method,path,body=b,headers=h)
        r=conn.getresponse();body=r.read();hs=dict(r.getheaders());status=r.status
        conn.close()
        try:body=json.loads(body)
        except (ValueError,UnicodeError):pass
        return status,body,hs

    def login(self):
        status,d,h=self.request('POST','/api/login',{'username':'astral','password':self.password})
        self.assertEqual(status,200,d)
        cookie=h['Set-Cookie'].split(';')[0]
        return cookie,d['csrf']

    def test_01_public_private_and_auth(self):
        status,d,_=self.request('GET','/api/data')
        self.assertEqual(status,200)
        self.assertFalse(d['authenticated'])
        self.assertTrue(all(x['visibility']=='public' for x in d['links']))
        public_urls={x['url'] for x in d['links']}
        self.assertTrue(any('panel.' in x for x in public_urls))
        self.assertTrue(any('down.' in x for x in public_urls))
        public_seed=sum(1 for x in SEED['links'] if x['visibility']=='public')
        self.assertEqual(len(d['links']),public_seed)
        self.assertEqual(d['catalog']['hidden'],len(SEED['links'])-public_seed)
        self.assertTrue(any(x['name']=='Navidrome' for x in d['links']))
        self.assertIsNone(d['csrf'])
        self.assertEqual(self.request('POST','/api/login',{'username':'错误用户名','password':self.password})[0],401)
        cookie,csrf=self.login()
        item={'category_id':d['categories'][0]['id'],'name':'Private Test','url':'https://private.example/','visibility':'private'}
        status,created,_=self.request('POST','/api/links',item,cookie,csrf)
        self.assertEqual(status,201)
        _,public,_=self.request('GET','/api/data')
        self.assertFalse(any(x['url']=='https://private.example/' for x in public['links']))
        self.assertEqual(public['catalog']['hidden'],len(SEED['links'])-public_seed+1)
        status,d,_=self.request('GET','/api/data',cookie=cookie)
        self.assertTrue(d['authenticated'])
        self.assertEqual(len(d['links']),len(SEED['links'])+1)
        self.assertEqual(self.request('DELETE',f"/api/links/{created['id']}",cookie=cookie,csrf=csrf)[0],200)
        self.assertTrue({x['url'] for x in SEED['links']}.issubset({x['url'] for x in d['links']}))
        self.assertEqual(self.request('GET','/api/export')[0],401)
        self.assertEqual(self.request('POST','/api/categories',{'name':'bad','rank':0})[0],401)
        self.assertEqual(self.request('POST','/api/categories',{'name':'bad','rank':0},cookie=cookie)[0],403)
        self.assertEqual(self.request('POST','/api/categories',{'name':'bad','rank':0},cookie=cookie,csrf='é')[0],403)
        self.assertEqual(self.request('POST','/api/categories',{'name':'bad','rank':0},cookie=cookie,csrf=csrf,origin='https://evil.example')[0],403)

    def test_02_crud_and_validation(self):
        cookie,csrf=self.login()
        status,d,_=self.request('POST','/api/categories',{'name':'测试分类','rank':99},cookie,csrf)
        self.assertEqual(status,201,d);cat=d['id']
        item={'category_id':cat,'name':'Test','url':'https://example.com/','description':'测试','icon':'star','visibility':'private','pinned':True,'rank':9,'aliases':'test'}
        status,d,_=self.request('POST','/api/links',item,cookie,csrf)
        self.assertEqual(status,201,d);ident=d['id']
        self.assertEqual(self.request('DELETE',f'/api/categories/{cat}',cookie=cookie,csrf=csrf)[0],409)
        self.assertEqual(self.request('PUT',f'/api/links/{ident}',dict(item,url='javascript:alert(1)'),cookie,csrf)[0],400)
        self.assertEqual(self.request('PUT',f'/api/links/{ident}',dict(item,url='https://user:pass@example.com'),cookie,csrf)[0],400)
        self.assertEqual(self.request('PUT',f'/api/links/{ident}',dict(item,name='Edited'),cookie,csrf)[0],200)
        self.assertEqual(self.request('DELETE',f'/api/links/{ident}',cookie=cookie,csrf=csrf)[0],200)
        self.assertEqual(self.request('DELETE',f'/api/categories/{cat}',cookie=cookie,csrf=csrf)[0],200)

    def test_03_export_import_transaction(self):
        cookie,csrf=self.login()
        status,backup,_=self.request('GET','/api/export',cookie=cookie)
        self.assertEqual(status,200)
        self.assertNotIn('admin',backup)
        self.assertNotIn('csrf',backup)
        broken=json.loads(json.dumps(backup));broken['links'][0]['url']='javascript:bad'
        self.assertEqual(self.request('POST','/api/import',broken,cookie,csrf)[0],400)
        _,after,_=self.request('GET','/api/export',cookie=cookie)
        self.assertEqual(after,backup,'Failed import must roll back all changes')
        self.assertEqual(self.request('POST','/api/import',backup,cookie,csrf)[0],200)
        self.assertEqual(self.request('PUT','/api/settings',{'musicUrl':'javascript:bad'},cookie,csrf)[0],400)
        self.assertEqual(self.request('PUT','/api/settings',{'subtitle':'安全测试'},cookie,csrf)[0],200)

    def test_035_large_and_empty_backup(self):
        cookie,csrf=self.login()
        _,original,_=self.request('GET','/api/export',cookie=cookie)
        large=json.loads(json.dumps(original))
        template=dict(large['links'][0])
        template.pop('catalog_key',None)
        template['url']='https://example.com/'+('a'*1800)
        large['links']=[dict(template,name=f'入口 {i}',rank=i) for i in range(650)]
        self.assertGreater(len(json.dumps(large).encode()),1024*1024)
        self.assertEqual(self.request('POST','/api/import',large,cookie,csrf)[0],200)
        _,exported,_=self.request('GET','/api/export',cookie=cookie)
        self.assertEqual(len(exported['links']),650)
        self.assertEqual(self.request('POST','/api/import',exported,cookie,csrf)[0],200)
        empty=dict(original,categories=[],links=[])
        self.assertEqual(self.request('POST','/api/import',empty,cookie,csrf)[0],200)
        _,exported,_=self.request('GET','/api/export',cookie=cookie)
        self.assertEqual(exported['categories'],[])
        self.assertEqual(self.request('POST','/api/import',exported,cookie,csrf)[0],200)
        self.assertEqual(self.request('POST','/api/import',original,cookie,csrf)[0],200)

    def test_036_launch_modes_backup_identity_and_assets(self):
        cookie,csrf=self.login()
        _,data,_=self.request('GET','/api/data',cookie=cookie)
        original=next(x for x in data['links'] if x.get('launch_mode')=='fnos')
        edited=dict(original,name='Launch mode test');edited.pop('launch_mode')
        self.assertEqual(self.request('PUT',f"/api/links/{original['id']}",edited,cookie,csrf)[0],200)
        _,changed,_=self.request('GET','/api/data',cookie=cookie)
        row=next(x for x in changed['links'] if x['id']==original['id'])
        self.assertEqual(row['launch_mode'],'fnos')
        self.assertEqual(row['catalog_key'],original['catalog_key'])
        self.assertEqual(self.request('PUT',f"/api/links/{original['id']}",dict(original,launch_mode='unsafe'),cookie,csrf)[0],400)
        self.assertEqual(self.request('PUT',f"/api/links/{original['id']}",original,cookie,csrf)[0],200)
        _,backup,_=self.request('GET','/api/export',cookie=cookie)
        broken=json.loads(json.dumps(backup));broken['links'][1]['catalog_key']=broken['links'][0]['catalog_key']
        self.assertEqual(self.request('POST','/api/import',broken,cookie,csrf)[0],400)
        self.assertEqual(self.request('GET','/api/export',cookie=cookie)[1],backup)
        status,html,h=self.request('GET','/nav')
        self.assertEqual(status,200)
        self.assertNotIn(b'__HOMEHUB_ASSET_VERSION__',html)
        self.assertIn(self.app.ASSET_VERSION.encode(),html)
        self.assertEqual(h['Cache-Control'],'no-cache')
        self.assertEqual(self.request('GET','/api/health')[1]['version'],self.app.VERSION)

    def test_037_autoplay_settings_and_legacy_import(self):
        cookie,csrf=self.login()
        _,original,_=self.request('GET','/api/export',cookie=cookie)
        try:
            self.assertEqual(self.request('PUT','/api/settings',{'musicAutoplay':True},cookie,csrf)[0],200)
            self.assertIs(self.request('GET','/api/data')[1]['settings']['musicAutoplay'],True)
            # An older client only knows site text: do not turn enabled autoplay off.
            self.assertEqual(self.request('PUT','/api/settings',{'subtitle':'Legacy client edit'},cookie,csrf)[0],200)
            self.assertIs(self.request('GET','/api/data')[1]['settings']['musicAutoplay'],True)
            for invalid in ('true','false',1,0,None,[],{}):
                with self.subTest(invalid=invalid):
                    before=self.request('GET','/api/data')[1]['settings']
                    self.assertEqual(self.request('PUT','/api/settings',{'musicAutoplay':invalid},cookie,csrf)[0],400)
                    self.assertEqual(self.request('GET','/api/data')[1]['settings'],before)
            self.assertEqual(self.request('PUT','/api/settings',{'musicAutoplay':False},cookie,csrf)[0],200)
            self.assertIs(self.request('GET','/api/data')[1]['settings']['musicAutoplay'],False)
            self.assertEqual(self.request('PUT','/api/settings',{'musicAutoplay':True},cookie,csrf)[0],200)
            _,backup,_=self.request('GET','/api/export',cookie=cookie)
            self.assertIs(backup['settings']['musicAutoplay'],True)
            invalid=json.loads(json.dumps(backup));invalid['settings']['musicAutoplay']='true'
            self.assertEqual(self.request('POST','/api/import',invalid,cookie,csrf)[0],400)
            self.assertEqual(self.request('GET','/api/export',cookie=cookie)[1],backup)
            backup['settings'].pop('musicAutoplay')
            self.assertEqual(self.request('POST','/api/import',backup,cookie,csrf)[0],200)
            self.assertIs(self.request('GET','/api/data')[1]['settings']['musicAutoplay'],False)
            # Legacy rows expose false without a write during a public read.
            with self.app.db() as c:
                legacy=json.loads(c.execute('SELECT data FROM settings').fetchone()[0])
                legacy.pop('musicAutoplay',None)
                c.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(legacy),))
            self.assertIs(self.request('GET','/api/data')[1]['settings']['musicAutoplay'],False)
            with self.app.db() as c:
                self.assertNotIn('musicAutoplay',json.loads(c.execute('SELECT data FROM settings').fetchone()[0]))
            self.assertEqual(self.request('PUT','/api/settings',{'subtitle':'Older row edit'},cookie,csrf)[0],200)
            self.assertIs(self.request('GET','/api/data')[1]['settings']['musicAutoplay'],False)
        finally:
            self.assertEqual(self.request('POST','/api/import',original,cookie,csrf)[0],200)

    def test_038_local_music_url_validation(self):
        cookie,csrf=self.login()
        original=self.request('GET','/api/data')[1]['settings']
        try:
            for valid in ('/media/music.mp3','/media/music.ogg?v=0123456789abcdef','/media/music.wav?v=aaaaaaaaaaaaaaaa','/media/music.m4a','/media/music.opus?v=0123456789abcdef'):
                with self.subTest(valid=valid):
                    self.assertEqual(self.request('PUT','/api/settings',{'musicUrl':valid},cookie,csrf)[0],200)
            for invalid in ('/media/other.mp3','/media/music.mp3?v=short','/media/music.mp3?v=0123456789abcdef&x=1','/media/music.mp3?x=0123456789abcdef','/media/music.mp3?v=0123456789abcdef#x','/media/../homehub.db','//evil.example/music.mp3'):
                with self.subTest(invalid=invalid):
                    self.assertEqual(self.request('PUT','/api/settings',{'musicUrl':invalid},cookie,csrf)[0],400)
        finally:
            self.assertEqual(self.request('PUT','/api/settings',original,cookie,csrf)[0],200)

    def test_04_music_and_range(self):
        cookie,csrf=self.login()
        audio=b'ID3'+b'\x00'*100
        status,d,_=self.request('POST','/api/music',audio,cookie,csrf,headers={'X-Filename':'sample.mp3','Content-Type':'audio/mpeg'},raw=True)
        self.assertEqual(status,200,d)
        self.assertEqual(d['musicUrl'],'/media/music.mp3?v='+hashlib.sha256(audio).hexdigest()[:16])
        self.assertEqual(d['musicTitle'],'sample')
        self.assertEqual(self.request('GET','/media/music.mp3')[0],200)
        status,b,h=self.request('GET',d['musicUrl'],headers={'Range':'bytes=0-15'})
        self.assertEqual(status,206)
        self.assertEqual(len(b),16)
        self.assertEqual(h['Content-Range'],'bytes 0-15/103')
        self.assertEqual(self.request('POST','/api/music',b'<html>bad</html>'*3,cookie,csrf,headers={'X-Filename':'sample.mp3'},raw=True)[0],400)
        self.assertEqual(self.request('GET','/assets/../seed.json')[0],404)
        self.assertEqual(self.request('GET','/assets/%2e%2e/app.py')[0],404)
        self.assertEqual(self.request('GET','/media/../homehub.db')[0],404)

    def test_041_music_upload_sets_default_and_preserves_settings(self):
        cookie,csrf=self.login()
        original=self.request('GET','/api/data')[1]['settings']
        try:
            self.assertEqual(self.request('PUT','/api/settings',{'title':'Custom site','subtitle':'Keep subtitle','tagline':'Keep tagline','musicAutoplay':True},cookie,csrf)[0],200)
            before=self.request('GET','/api/data')[1]['settings']
            audio=b'ID3'+b'first audio fixture'*8
            headers={'X-Filename':quote('C:\\fakepath\\银河\x00夜曲.mp3'),'Content-Type':'audio/mpeg'}
            status,uploaded,_=self.request('POST','/api/music',audio,cookie,csrf,headers=headers,raw=True)
            self.assertEqual(status,200,uploaded)
            self.assertEqual(uploaded['musicTitle'],'银河夜曲')
            self.assertEqual(uploaded['musicUrl'],'/media/music.mp3?v='+hashlib.sha256(audio).hexdigest()[:16])
            settings=self.request('GET','/api/data')[1]['settings']
            self.assertEqual(settings,dict(before,musicUrl=uploaded['musicUrl'],musicTitle='银河夜曲'))
            self.assertEqual((Path(self.temp.name)/'media/music.mp3').read_bytes(),audio)
            self.assertEqual((Path(self.temp.name)/'media/music.mp3').stat().st_mode&0o777,0o600)
            repeat=self.request('POST','/api/music',audio,cookie,csrf,headers=headers,raw=True)[1]
            self.assertEqual(repeat['musicUrl'],uploaded['musicUrl'])
            changed=self.request('POST','/api/music',audio+b'new',cookie,csrf,headers=headers,raw=True)[1]
            self.assertNotEqual(changed['musicUrl'],uploaded['musicUrl'])
            long_headers={'X-Filename':quote('../'+'曲'*120+'.MP3')}
            status,long_title,_=self.request('POST','/api/music',audio,cookie,csrf,headers=long_headers,raw=True)
            self.assertEqual(status,200)
            self.assertEqual(long_title['musicTitle'],'曲'*100)
            self.assertEqual(long_title['musicUrl'],uploaded['musicUrl'])
            self.assertEqual(list((Path(self.temp.name)/'media').glob('.upload-*')),[])
        finally:
            self.assertEqual(self.request('PUT','/api/settings',original,cookie,csrf)[0],200)

    def test_042_music_rejections_do_not_write(self):
        cookie,csrf=self.login()
        fixture=b'ID3'+b'prior valid audio'*8
        self.assertEqual(self.request('POST','/api/music',fixture,cookie,csrf,headers={'X-Filename':'prior.mp3'},raw=True)[0],200)
        path=Path(self.temp.name)/'media/music.mp3'
        before_file=path.read_bytes()
        before_settings=self.request('GET','/api/data')[1]['settings']
        audio=b'ID3'+b'not written'*10
        cases=[
            (401,None,None,{'X-Filename':'good.mp3'},audio),
            (403,cookie,None,{'X-Filename':'good.mp3'},audio),
            (400,cookie,csrf,{'X-Filename':'bad.exe'},audio),
            (400,cookie,csrf,{'X-Filename':'bad.mp3'},b'<html>not audio</html>'),
            (400,cookie,csrf,{'X-Filename':'small.mp3'},b'ID3'),
            (413,cookie,csrf,{'X-Filename':'big.mp3','Content-Length':str(self.app.MAX_AUDIO+1)},b''),
        ]
        for expected,auth,token,headers,payload in cases:
            with self.subTest(expected=expected,headers=headers):
                self.assertEqual(self.request('POST','/api/music',payload,auth,token,headers=headers,raw=True)[0],expected)
                self.assertEqual(path.read_bytes(),before_file)
                self.assertEqual(self.request('GET','/api/data')[1]['settings'],before_settings)
        self.assertEqual(list((Path(self.temp.name)/'media').glob('.upload-*')),[])

    def test_043_supported_music_formats(self):
        cookie,csrf=self.login()
        original=self.request('GET','/api/data')[1]['settings']
        try:
            samples={'mp3':b'ID3'+b'0'*20,'ogg':b'OggS'+b'0'*20,
                     'wav':b'RIFF0000WAVE'+b'0'*20,'m4a':b'0000ftyp'+b'0'*20,'opus':b'OggS'+b'0'*20}
            for ext,audio in samples.items():
                with self.subTest(ext=ext):
                    status,d,_=self.request('POST','/api/music',audio,cookie,csrf,headers={'X-Filename':'track.'+ext},raw=True)
                    self.assertEqual(status,200,d)
                    self.assertEqual(d['musicUrl'],f'/media/music.{ext}?v='+hashlib.sha256(audio).hexdigest()[:16])
                    self.assertEqual(d['musicTitle'],'track')
                    self.assertEqual(self.request('GET',d['musicUrl'])[1],audio)
        finally:
            self.assertEqual(self.request('PUT','/api/settings',original,cookie,csrf)[0],200)

    def test_045_bootstrap_recovery(self):
        old_data,old_db=self.app.DATA,self.app.DB
        with tempfile.TemporaryDirectory(prefix='homehub-bootstrap-') as folder:
            self.app.DATA=Path(folder)
            self.app.DB=Path(folder)/'homehub.db'
            pending='Pending-Test-Password-12345'
            (Path(folder)/'initial-admin-password').write_text(pending+'\n')
            try:
                self.app.init()
                with self.app.db() as c:
                    row=c.execute('SELECT password FROM admin').fetchone()
                    self.assertTrue(self.app.check_password(pending,row[0]))
                self.assertEqual((Path(folder)/'initial-admin-password').stat().st_mode&0o777,0o600)
            finally:
                self.app.DATA,self.app.DB=old_data,old_db

    def test_05_cookie_and_logout(self):
        status,d,h=self.request('POST','/api/login',{'username':'astral','password':self.password})
        cookie=h['Set-Cookie'].split(';')[0]
        for flag in ['HttpOnly','SameSite=Strict','Secure','Path=/']:
            self.assertIn(flag,h['Set-Cookie'])
        self.assertEqual(self.request('POST','/api/logout',{},cookie,d['csrf'])[0],200)
        self.assertFalse(self.request('GET','/api/data',cookie=cookie)[1]['authenticated'])

    def test_99_password_revokes_other_sessions(self):
        cookie,csrf=self.login()
        other,_=self.login()
        self.assertEqual(self.request('POST','/api/password',{'currentPassword':'wrong','newPassword':'New-Password-Test-12345'},cookie,csrf)[0],400)
        self.assertEqual(self.request('POST','/api/password',{'currentPassword':self.password,'newPassword':'New-Password-Test-12345'},cookie,csrf)[0],200)
        self.assertFalse(self.request('GET','/api/data',cookie=other)[1]['authenticated'])
        self.assertTrue(self.request('GET','/api/data',cookie=cookie)[1]['authenticated'])
        self.assertFalse((Path(self.temp.name)/'initial-admin-password').exists())
    def test_91_monitor_page_and_metrics_proxy(self):
        status,body,headers=self.request('GET','/monitor')
        self.assertEqual(status,200)
        self.assertIn('text/html',headers.get('Content-Type',''))
        # Metrics are LAN-public reads: no login required, collector errors stay 503.
        status,body,_=self.request('GET','/api/metrics/snapshot')
        self.assertEqual(status,503)
        self.assertTrue(body['error'])
        status,body,_=self.request('GET','/api/metrics/history?range=2h')
        self.assertEqual(status,400)
        status,body,_=self.request('GET','/api/metrics/history?range=24h')
        self.assertEqual(status,503)

    def test_92_metrics_proxy_passthrough(self):
        import http.server, threading
        class Stub(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                payload={'sample':{'t':1},'containers':[]}
                if self.path.startswith('/history'):
                    payload={'range':'24h','system':{'t':[1],'cpu':[2.0]}}
                body=json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Stub)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        old=self.app.COLLECTOR
        self.app.COLLECTOR=f'http://127.0.0.1:{server.server_address[1]}'
        try:
            status,body,_=self.request('GET','/api/metrics/snapshot')
            self.assertEqual(status,200)
            self.assertEqual(body['sample']['t'],1)
            status,body,_=self.request('GET','/api/metrics/history?range=24h&container=web')
            self.assertEqual(status,200)
            self.assertEqual(body['system']['cpu'][0],2.0)
        finally:
            self.app.COLLECTOR=old
            server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
