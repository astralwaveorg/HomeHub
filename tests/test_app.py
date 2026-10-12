"""Run locally: python3 -m unittest discover -s homehub/tests -v.
All credentials and database files are generated only in a temporary directory.
"""
import http.client
import importlib.util
import json
import os
import sys
from pathlib import Path
import tempfile
import threading
import unittest

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
        self.assertEqual(len(d['links']),len(SEED['links']))
        self.assertEqual(d['catalog']['hidden'],0)
        self.assertTrue(any(x['name']=='Navidrome' for x in d['links']))
        self.assertIsNone(d['csrf'])
        self.assertEqual(self.request('POST','/api/login',{'username':'错误用户名','password':self.password})[0],401)
        cookie,csrf=self.login()
        item={'category_id':d['categories'][0]['id'],'name':'Private Test','url':'https://private.example/','visibility':'private'}
        status,created,_=self.request('POST','/api/links',item,cookie,csrf)
        self.assertEqual(status,201)
        _,public,_=self.request('GET','/api/data')
        self.assertFalse(any(x['url']=='https://private.example/' for x in public['links']))
        self.assertEqual(public['catalog']['hidden'],1)
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

    def test_04_music_and_range(self):
        cookie,csrf=self.login()
        audio=b'ID3'+b'\x00'*100
        status,d,_=self.request('POST','/api/music',audio,cookie,csrf,headers={'X-Filename':'sample.mp3','Content-Type':'audio/mpeg'},raw=True)
        self.assertEqual(status,200,d)
        status,b,h=self.request('GET',d['musicUrl'],headers={'Range':'bytes=0-15'})
        self.assertEqual(status,206)
        self.assertEqual(len(b),16)
        self.assertEqual(h['Content-Range'],'bytes 0-15/103')
        self.assertEqual(self.request('POST','/api/music',b'<html>bad</html>'*3,cookie,csrf,headers={'X-Filename':'sample.mp3'},raw=True)[0],400)
        self.assertEqual(self.request('GET','/assets/../seed.json')[0],404)
        self.assertEqual(self.request('GET','/assets/%2e%2e/app.py')[0],404)
        self.assertEqual(self.request('GET','/media/../homehub.db')[0],404)

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

if __name__=='__main__':unittest.main()
