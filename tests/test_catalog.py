"""Catalog upgrades must not replace runtime data or user decisions."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import catalog

SEED = json.loads((ROOT/'seed.json').read_text())
OLD = json.loads((ROOT/'tests/fixtures/catalog-v1.0.0.json').read_text())


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='homehub-catalog-')
        self.env = dict(os.environ, HOMEHUB_DATA=self.temp.name)
        spec = importlib.util.spec_from_file_location('catalog_test_app', ROOT/'app.py')
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.app.DATA = Path(self.temp.name)
        self.app.DB = self.app.DATA/'homehub.db'

    def tearDown(self):
        self.temp.cleanup()

    def old_database(self):
        with self.app.db() as c:
            c.executescript('''
            CREATE TABLE categories(id INTEGER PRIMARY KEY,name TEXT NOT NULL,rank INTEGER NOT NULL);
            CREATE TABLE links(id INTEGER PRIMARY KEY,category_id INTEGER NOT NULL REFERENCES categories(id),name TEXT NOT NULL,url TEXT NOT NULL,description TEXT NOT NULL,icon TEXT NOT NULL,visibility TEXT NOT NULL,pinned INTEGER NOT NULL,rank INTEGER NOT NULL,aliases TEXT NOT NULL);
            CREATE TABLE settings(id INTEGER PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE admin(id INTEGER PRIMARY KEY,username TEXT NOT NULL,password TEXT NOT NULL);
            CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires INTEGER NOT NULL);
            ''')
            for cat in OLD['categories']:
                c.execute('INSERT INTO categories VALUES(?,?,?)',(cat['id'],cat['name'],cat['rank']))
            for item in OLD['links']:
                names=['category_id','name','url','description','icon','visibility','pinned','rank','aliases']
                c.execute('INSERT INTO links('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(item[n] for n in names))
            c.execute('INSERT INTO settings VALUES(1,?)',(json.dumps({'title':'My custom title','musicUrl':'','subtitle':'Keep my settings'}),))
            c.execute('INSERT INTO admin VALUES(1,?,?)',('owner','unchanged-test-hash'))
            c.execute('INSERT INTO sessions VALUES(?,?,?)',('session-hash','csrf-value',9999999999))
            c.execute("UPDATE links SET name='Owner audiobook label',rank=-99 WHERE url='https://books.homehub.stream:8443/'")
            c.execute("UPDATE categories SET name='Owner media group' WHERE id=1")

    def snapshot_auth_settings(self):
        with self.app.db() as c:
            return {name:[tuple(x) for x in c.execute('SELECT * FROM '+name)] for name in ('admin','settings','sessions')}

    def sync(self, publish=False, seed=None):
        with self.app.db() as c:
            c.execute('BEGIN IMMEDIATE')
            return catalog.sync(c, seed or SEED, self.app.link_fields, publish_existing=publish)

    def test_legacy_upgrade_and_explicit_publication_preserve_owner(self):
        self.old_database();before=self.snapshot_auth_settings()
        result=self.sync(publish=True)
        self.assertEqual(result['added'],len(SEED['links'])-len(OLD['links']))
        self.assertEqual(result['public'],len(SEED['links']))
        self.assertEqual(self.snapshot_auth_settings(),before)
        self.assertFalse((self.app.DATA/'initial-admin-password').exists())
        with self.app.db() as c:
            music=c.execute("SELECT * FROM links WHERE catalog_key='website:music'").fetchone()
            self.assertEqual(music['name'],'Navidrome')
            book=c.execute("SELECT * FROM links WHERE catalog_key='website:books'").fetchone()
            self.assertEqual((book['name'],book['rank']),('Owner audiobook label',-99))
            self.assertEqual(c.execute('SELECT name FROM categories WHERE id=?',(book['category_id'],)).fetchone()[0],'Owner media group')
        again=self.sync()
        self.assertEqual(again['added'],0)
        self.assertEqual(again['renamed'],0)
        self.assertEqual(again['published'],0)

    def test_regular_sync_preserves_edited_url_private_and_deletions(self):
        self.old_database();self.sync(publish=True)
        with self.app.db() as c:
            c.execute("UPDATE links SET name='My private music',url='https://music.example.test/',visibility='private',rank=-50 WHERE catalog_key='website:music'")
            c.execute("DELETE FROM links WHERE catalog_key='website:ddns'")
        summary=self.sync()
        self.assertEqual(summary['added'],0)
        self.assertEqual(summary['skippedDeleted'],1)
        with self.app.db() as c:
            row=c.execute("SELECT * FROM links WHERE catalog_key='website:music'").fetchone()
            self.assertEqual((row['name'],row['url'],row['visibility'],row['rank']),('My private music','https://music.example.test/','private',-50))
            self.assertFalse(c.execute("SELECT 1 FROM links WHERE catalog_key='website:ddns'").fetchone())

    def test_regular_upgrade_does_not_publish_existing_private_links(self):
        self.old_database();result=self.sync()
        self.assertEqual(result['published'],0)
        with self.app.db() as c:
            self.assertEqual(c.execute("SELECT visibility FROM links WHERE catalog_key='website:panel'").fetchone()[0],'private')

    def test_cli_does_not_initialize_or_touch_admin_or_sessions(self):
        self.old_database();before=self.snapshot_auth_settings()
        p=subprocess.run([sys.executable,str(ROOT/'app.py'),'sync-catalog','--publish-existing'],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        result=json.loads(p.stdout)
        self.assertEqual(result['total'],len(SEED['links']))
        self.assertEqual(self.snapshot_auth_settings(),before)
        self.assertFalse((self.app.DATA/'initial-admin-password').exists())

    def test_invalid_new_catalog_rolls_back(self):
        self.old_database();self.sync()
        broken=json.loads(json.dumps(SEED))
        extra=dict(broken['links'][0],catalog_key='test:new',url='javascript:bad')
        broken['links'].append(extra)
        with self.assertRaises(self.app.APIError):self.sync(seed=broken)
        with self.app.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM links').fetchone()[0],len(SEED['links']))
            self.assertFalse(c.execute("SELECT 1 FROM catalog_registry WHERE key='test:new'").fetchone())

    def test_removed_category_and_defaults_are_not_revived(self):
        self.old_database();self.sync()
        extra=json.loads(json.dumps(SEED))
        extra['categories'].append({'id':99,'name':'Temporary','rank':99,'catalog_key':'category:temporary'})
        extra['links'].append(dict(extra['links'][0],catalog_key='test:temporary',category_id=99,url='https://temporary.example.test/'))
        self.sync(seed=extra)
        with self.app.db() as c:
            c.execute("DELETE FROM links WHERE catalog_key='test:temporary'")
            c.execute("DELETE FROM categories WHERE catalog_key='category:temporary'")
        result=self.sync(seed=extra)
        self.assertEqual(result['added'],0)
        self.assertEqual(result['categoriesAdded'],0)

    def test_private_custom_replacement_is_not_adopted_or_published(self):
        self.old_database();self.sync(publish=True)
        template=next(x for x in SEED['links'] if x['catalog_key']=='website:love')
        with self.app.db() as c:
            oldcat=c.execute("SELECT category_id FROM links WHERE catalog_key='website:love'").fetchone()[0]
            c.execute('DELETE FROM links WHERE category_id=?',(oldcat,))
            c.execute('DELETE FROM categories WHERE id=?',(oldcat,))
            cat=c.execute('INSERT INTO categories(name,rank) VALUES(?,?)',('生活空间',40)).lastrowid
            custom=self.app.link_fields(dict(template,category_id=cat,name='Private replacement',visibility='private'),c)
            ident=c.execute('INSERT INTO links('+','.join(custom)+') VALUES('+','.join('?' for _ in custom)+')',tuple(custom.values())).lastrowid
        for publish in (False,True):
            self.sync(publish=publish)
            with self.app.db() as c:
                row=c.execute('SELECT * FROM links WHERE id=?',(ident,)).fetchone()
                self.assertEqual(row['visibility'],'private')
                self.assertIsNone(row['catalog_key'])
                self.assertIsNone(c.execute('SELECT catalog_key FROM categories WHERE id=?',(cat,)).fetchone()[0])

    def test_seed_coverage_and_native_launch_modes(self):
        catalog.validate_seed(SEED)
        self.assertEqual(len(SEED['links']),41)
        self.assertTrue(all(x['visibility']=='public' for x in SEED['links']))
        self.assertEqual(sum(x.get('launch_mode')=='fnos' for x in SEED['links']),5)
        old_urls={x['url'] for x in OLD['links']}
        self.assertTrue(old_urls.issubset({x['url'] for x in SEED['links']}))


if __name__=='__main__':unittest.main()
