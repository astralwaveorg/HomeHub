"""Idempotent catalog upgrades: retain admin edits and intentional deletions.

Keys are durable source identities, not URLs or display names. The registry remembers
seen defaults even after deletion, preventing normal releases from reviving them.
"""
from collections import Counter
import re

KEY = re.compile(r'[a-z0-9][a-z0-9_.:-]{0,79}')


def valid_key(value):
    return isinstance(value, str) and KEY.fullmatch(value) is not None


def ensure_schema(c):
    for table in ('categories', 'links'):
        if 'catalog_key' not in {row['name'] for row in c.execute(f'PRAGMA table_info({table})')}:
            c.execute(f'ALTER TABLE {table} ADD COLUMN catalog_key TEXT')
        c.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS {table}_catalog_key ON {table}(catalog_key)')
    if 'launch_mode' not in {row['name'] for row in c.execute('PRAGMA table_info(links)')}:
        c.execute("ALTER TABLE links ADD COLUMN launch_mode TEXT NOT NULL DEFAULT 'direct'")
    c.execute('CREATE TABLE IF NOT EXISTS catalog_registry(kind TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(kind,key))')


def remember(c, kind, key):
    c.execute('INSERT OR IGNORE INTO catalog_registry(kind,key) VALUES(?,?)', (kind, key))


def registry(c):
    return [dict(row) for row in c.execute('SELECT kind,key FROM catalog_registry ORDER BY kind,key')]


def validate_seed(seed):
    if not isinstance(seed, dict) or not isinstance(seed.get('categories'), list) or not isinstance(seed.get('links'), list):
        raise ValueError('Catalog must contain categories and links')
    ids, keys, urls = set(), set(), set()
    if len(seed['categories']) > 100 or len(seed['links']) > 2000:
        raise ValueError('Catalog exceeds supported size')
    for item in seed['categories']:
        if type(item.get('id')) is not int or item['id'] <= 0 or item['id'] in ids or not valid_key(item.get('catalog_key')) or item['catalog_key'] in keys:
            raise ValueError('Catalog category IDs/keys must be unique and valid')
        ids.add(item['id']); keys.add(item['catalog_key'])
    keys.clear()
    for item in seed['links']:
        if item.get('category_id') not in ids or not valid_key(item.get('catalog_key')) or item['catalog_key'] in keys or item.get('url') in urls:
            raise ValueError('Catalog links need valid categories and unique keys/URLs')
        if not isinstance(item.get('legacy_names', []), list) or any(not isinstance(x, str) for x in item.get('legacy_names', [])):
            raise ValueError('Legacy names must be a string list')
        keys.add(item['catalog_key']); urls.add(item.get('url'))


def sync(c, seed, link_fields, publish_existing=False):
    """Run inside an explicit caller-owned transaction; never touch auth/settings."""
    validate_seed(seed)
    ensure_schema(c)
    summary = dict(added=0, adopted=0, published=0, renamed=0,
                   skippedDeleted=0, categoriesAdded=0)
    category_ids = {}
    for item in seed['categories']:
        key = item['catalog_key']
        row = c.execute('SELECT * FROM categories WHERE catalog_key=?', (key,)).fetchone()
        if row is None and c.execute('SELECT 1 FROM catalog_registry WHERE kind=? AND key=?', ('category', key)).fetchone():
            continue
        if row is None:
            row = c.execute('SELECT * FROM categories WHERE name=? AND catalog_key IS NULL ORDER BY id LIMIT 1', (item['name'],)).fetchone()
        if row is None:
            # Bootstrap association also recognizes owner-renamed categories via their links.
            candidates = []
            for link in seed['links']:
                if link['category_id'] == item['id']:
                    r = c.execute('SELECT category_id FROM links WHERE url=?', (link['url'],)).fetchone()
                    if r:
                        candidates.append(r['category_id'])
            for candidate, _ in Counter(candidates).most_common():
                row = c.execute('SELECT * FROM categories WHERE id=? AND catalog_key IS NULL', (candidate,)).fetchone()
                if row:
                    break
        if row is None:
            ident = c.execute('INSERT INTO categories(name,rank,catalog_key) VALUES(?,?,?)', (item['name'], item['rank'], key)).lastrowid
            summary['categoriesAdded'] += 1
        else:
            ident = row['id']
            c.execute('UPDATE categories SET catalog_key=? WHERE id=?', (key, ident))
        category_ids[item['id']] = ident
        remember(c, 'category', key)
    for item in seed['links']:
        key = item['catalog_key']
        row = c.execute('SELECT * FROM links WHERE catalog_key=?', (key,)).fetchone()
        if row is None and c.execute('SELECT 1 FROM catalog_registry WHERE kind=? AND key=?', ('link', key)).fetchone():
            summary['skippedDeleted'] += 1
            continue
        if row is None:
            row = c.execute('SELECT * FROM links WHERE url=? AND catalog_key IS NULL ORDER BY id LIMIT 1', (item['url'],)).fetchone()
            if row:
                c.execute('UPDATE links SET catalog_key=? WHERE id=?', (key, row['id']))
                summary['adopted'] += 1
        if row is None:
            if item['category_id'] not in category_ids:
                summary['skippedDeleted'] += 1
                continue
            values = link_fields(dict(item, category_id=category_ids[item['category_id']]), c)
            values['catalog_key'] = key
            c.execute('INSERT INTO links(' + ','.join(values) + ') VALUES(' + ','.join('?' for _ in values) + ')', tuple(values.values()))
            summary['added'] += 1
        else:
            if publish_existing and row['visibility'] != 'public':
                c.execute("UPDATE links SET visibility='public' WHERE id=?", (row['id'],))
                summary['published'] += 1
            if row['name'] in item.get('legacy_names', []):
                c.execute('UPDATE links SET name=? WHERE id=?', (item['name'], row['id']))
                summary['renamed'] += 1
        remember(c, 'link', key)
    summary['total'] = c.execute('SELECT count(*) FROM links').fetchone()[0]
    summary['public'] = c.execute("SELECT count(*) FROM links WHERE visibility='public'").fetchone()[0]
    return summary
