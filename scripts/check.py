#!/usr/bin/env python3
"""Dependency-free developer/CI gate; never accesses production data."""
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def catalog_check(root=ROOT):
    seed = json.loads((root / 'seed.json').read_text())
    categories = seed['categories']
    ids = [c['id'] for c in categories]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate category IDs')
    category_keys = [c.get('catalog_key') for c in categories if c.get('catalog_key')]
    link_keys = [x.get('catalog_key') for x in seed['links'] if x.get('catalog_key')]
    if len(category_keys) != len(set(category_keys)) or len(link_keys) != len(set(link_keys)):
        raise ValueError('Duplicate stable catalog keys')
    urls = set()
    for link in seed['links']:
        u = urlsplit(link['url'])
        if (link['category_id'] not in ids or not link['name'].strip()
                or link['visibility'] not in ('public', 'private')
                or u.scheme not in ('http', 'https') or not u.hostname
                or u.username or u.password or link['url'] in urls):
            raise ValueError('Invalid or duplicate catalog entry: ' + link['name'])
        urls.add(link['url'])
    # Parse every tracked JSON catalog/document, excluding local/generated state.
    for path in (root / 'static').rglob('*.json'):
        json.loads(path.read_text())
    print(f'Catalog consistent: {len(categories)} categories, {len(urls)} links', flush=True)


def main():
    catalog_check()
    for suite in ('tests', 'scripts'):
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', suite, '-p', 'test_*.py', '-v'], cwd=ROOT, check=True)
    javascript = [p for folder in ('static','scripts') for pattern in ('*.js','*.mjs') for p in (ROOT/folder).rglob(pattern)]
    for path in sorted(javascript):
        subprocess.run(['node', '--check', str(path)], cwd=ROOT, check=True)
    print('All checks passed.')


if __name__ == '__main__':
    main()
