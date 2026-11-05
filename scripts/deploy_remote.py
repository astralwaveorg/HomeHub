#!/usr/bin/env python3
"""NAS-only executor. Fixed scope; backups never leave the NAS. Requires Python 3."""
import argparse
from contextlib import contextmanager
import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import tarfile
import time
import urllib.request
import uuid

ROOT = Path('/vol1/1000/docker/homehub')
RUNTIME = {'app.py', 'catalog.py', 'collector.py', 'icon_library.py', 'seed.json', 'ambient.wav', 'Dockerfile', 'compose.yaml', 'VERSION', '.dockerignore', 'README.md', 'CHANGELOG.md'}


def safe(path):
    path = Path(path)
    if path != ROOT and ROOT not in path.parents:
        raise ValueError('Path outside Homehub')
    if ROOT.resolve() != ROOT:
        raise ValueError('Homehub root must not be a symlink')
    for part in [path, *path.parents]:
        if part == ROOT.parent: break
        if part.is_symlink(): raise ValueError('Symlinks forbidden: ' + str(part))
    return path


def runtime(name):
    p = PurePosixPath(name)
    return (not p.is_absolute() and '..' not in p.parts and
            (name in RUNTIME or (len(p.parts) > 1
                                 and (p.parts[0] == 'static' or (p.parts[0] == 'docs' and p.suffix == '.md'))
                                 and all(not x.startswith('.') for x in p.parts))))


def run(*args, capture=False, env=None):
    # Do not echo output of Docker build/exec/compose: it may contain app diagnostics.
    result = subprocess.run(args, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('Command failed (output withheld): ' + ' '.join(args[:3]))
    return result.stdout.decode().strip() if capture else None


def atomic(path, content, public=False):
    path = safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if public:
        # Docker COPY preserves permissions; UID 1000 must read public source/assets.
        parent = path.parent
        while parent != ROOT:
            os.chmod(safe(parent), 0o755)
            parent = parent.parent
    temp = safe(path.with_name(path.name + '.release-tmp'))
    with open(temp, 'wb') as f:
        f.write(content); f.flush(); os.fsync(f.fileno())
        os.fchmod(f.fileno(), 0o644 if public else 0o600)
    os.replace(temp, path)


@contextmanager
def lock():
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(safe(ROOT / '.release.lock'), 'a') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another deployment is running')
        yield


def current_image():
    image = run('docker', 'inspect', '--format', '{{.Image}}', 'homehub', capture=True)
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Invalid live image ID')
    # Retain code even when the original mutable 1.0.0 tag is overwritten elsewhere.
    retained = 'homehub-portal:retained-' + image[7:23]
    run('docker', 'tag', image, retained)
    return image, retained


def source_files():
    result = [safe(ROOT / n) for n in sorted(RUNTIME) if safe(ROOT / n).is_file()]
    for directory in ('static', 'docs'):
        base = safe(ROOT / directory)
        if base.exists():
            for p in sorted(base.rglob('*')):
                safe(p)
                if p.is_file() and runtime(str(p.relative_to(ROOT))): result.append(p)
    return result


def backup():
    name = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
    dest = safe(ROOT / 'backups' / name)
    dest.mkdir(parents=True, mode=0o700)
    os.chmod(safe(ROOT / 'backups'), 0o700)
    image, retained = current_image()
    metadata = {'image_id': image, 'image': retained, 'source_files': []}
    for p in source_files():
        rel = p.relative_to(ROOT)
        out = safe(dest / 'source' / rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, out)
        metadata['source_files'].append(str(rel))
    database = safe(ROOT / 'data/homehub.db')
    if not database.is_file(): raise RuntimeError('Existing live database required; refusing bootstrap')
    # Host SQLite online backup reads WAL correctly; no password or session is printed.
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=30) as src:
        with sqlite3.connect(dest / 'homehub.db') as dst:
            src.backup(dst)
            if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup database integrity check failed')
    os.chmod(dest / 'homehub.db', 0o600)
    media = safe(ROOT / 'data/media')
    if media.exists():
        for p in media.rglob('*'): safe(p)
        shutil.copytree(media, safe(dest / 'media'))
    # The owner-editable catalog overlay is not part of the image; keep it with the backup.
    for extra in ('seed.json', 'seed.json.bak'):
        overlay = safe(ROOT / 'data' / extra)
        if overlay.is_file() and not overlay.is_symlink():
            shutil.copyfile(overlay, safe(dest / extra))
            metadata.setdefault('catalog_files', []).append(extra)
    atomic(dest / 'runtime.json', (json.dumps(metadata, indent=2) + '\n').encode())
    print('NAS backup: ' + name, flush=True)
    return name, metadata


def compose(image):
    env = dict(os.environ, HOMEHUB_IMAGE=image)
    # Explicit project directory/file prevents accidentally acting on another stack.
    run('docker', 'compose', '--project-directory', str(ROOT), '-f', str(ROOT / 'compose.yaml'),
        'up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'homehub', 'collector', env=env)


def health(public_url, expected_version=None):
    from urllib.parse import urlsplit
    parsed = urlsplit(public_url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Public URL must be credential-free HTTPS origin')
    targets = ['http://127.0.0.1:48080/api/health', public_url.rstrip('/') + '/api/health',
               public_url.rstrip('/') + '/', public_url.rstrip('/') + '/nav']
    for attempt in range(24):
        try:
            for url in targets:
                with urllib.request.urlopen(url, timeout=5) as response:
                    if response.status != 200: raise ValueError('HTTP health failed')
                    body = response.read()
                    if url.endswith('/api/health'):
                        d = json.loads(body)
                        if d.get('ok') is not True: raise ValueError('Backend reports unhealthy')
                        if expected_version and d.get('version') != expected_version:
                            raise ValueError('Health version mismatch')
            return
        except Exception:
            if attempt == 23: raise RuntimeError('Backend/public health checks failed')
            time.sleep(2)


def collector_health(attempts=24):
    # The collector has no host port; verify it through the portal's compose network.
    check = "import urllib.request;urllib.request.urlopen('http://collector:9100/health',timeout=4)"
    for attempt in range(attempts):
        try:
            run('docker', 'exec', 'homehub', 'python', '-c', check)
            return
        except RuntimeError:
            if attempt == attempts - 1: raise RuntimeError('Collector health check failed')
            time.sleep(2)


def remove_collector():
    # Best-effort cleanup when rolling back to a compose file without the service.
    try:
        subprocess.run(['docker', 'rm', '-f', 'homehub-collector'], cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        pass


def restore(name, public_url):
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', name):
        raise ValueError('Invalid backup basename')
    dest = safe(ROOT / 'backups' / name)
    meta = json.loads(safe(dest / 'runtime.json').read_text())
    image = meta['image']
    if not re.fullmatch(r'homehub-portal:retained-[0-9a-f]{16}', image): raise ValueError('Invalid retained image')
    actual = run('docker', 'image', 'inspect', '--format', '{{.Id}}', image, capture=True)
    if actual != meta['image_id']: raise ValueError('Retained image mismatch')
    for name in meta['source_files']:
        if not runtime(name): raise ValueError('Invalid source path in backup')
        content = safe(dest / 'source' / name).read_bytes()
        if name == 'compose.yaml': content = pin_compose(content, image)
        atomic(ROOT / name, content, public=True)
    remove_collector()
    compose(image)
    health(public_url)
    print('Code/image rollback healthy; database and media were NOT restored', flush=True)


def pin_compose(content, image):
    text = content.decode()
    text, count = re.subn(r'(?m)^    image:.*$', '    image: ${HOMEHUB_IMAGE:-' + image + '}', text)
    # Older single-service backups stay rollback-able; deploy() asserts the new count.
    if count < 1: raise ValueError('Expected at least one service image declaration')
    return text.encode()


def deploy(stage, public_url, publish_existing=False):
    stage = safe(Path(stage))
    if stage.parent != ROOT / 'releases' or not re.fullmatch('[0-9a-f]{40}', stage.name):
        raise ValueError('Invalid release stage')
    m = json.loads(safe(stage / 'manifest.json').read_text())
    archive = safe(stage / 'archive.tar').read_bytes()
    if m['revision'] != stage.name or hashlib.sha256(archive).hexdigest() != m['sha256']:
        raise ValueError('Release integrity mismatch')
    if not re.fullmatch(r'\d+\.\d+\.\d+', m['version']): raise ValueError('Invalid version')
    source = safe(stage / 'source')
    source.mkdir(exist_ok=True)
    contents = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
        for member in tf.getmembers():
            if not runtime(member.name): continue
            if member.isdir(): continue
            if not member.isfile(): raise ValueError('Only regular runtime files allowed')
            contents[member.name] = tf.extractfile(member).read()
    if not RUNTIME.issubset(contents): raise ValueError('Runtime incomplete')
    if len(re.findall(r'(?m)^    image:', contents['compose.yaml'].decode())) != 2:
        raise ValueError('compose.yaml must declare exactly the portal and collector services')
    for name, data in contents.items(): atomic(source / name, data, public=True)
    image = 'homehub-portal:' + m['version'] + '-' + m['revision']
    print('Preparing immutable image: ' + image, flush=True)
    # Never rebuild an existing immutable tag. A failed previous build is safely retried.
    try:
        label = run('docker', 'image', 'inspect', '--format', '{{index .Config.Labels "org.opencontainers.image.revision"}}', image, capture=True)
    except RuntimeError:
        build = ['docker', 'build', '--label', 'org.opencontainers.image.revision=' + m['revision']]
        proxy = os.environ.get('HOMEHUB_BUILD_PROXY', '').strip()
        if proxy:
            for key in ('HTTP_PROXY', 'HTTPS_PROXY'):
                build += ['--build-arg', f'{key}={proxy}']
        run(*build, '-t', image, str(source))
    else:
        if label != m['revision']: raise ValueError('Existing immutable image tag has wrong revision')
    print('Image ready; backing up live SQLite/media and previous runtime on NAS', flush=True)
    name, _ = backup()
    try:
        # Service is briefly stopped during source replacement; only this container is touched.
        run('docker', 'stop', 'homehub')
        for filename, data in contents.items():
            if filename == 'compose.yaml': data = pin_compose(data, image)
            atomic(ROOT / filename, data, public=True)
        compose(image)
        health(public_url, m['version'])
        collector_health()
        args = ['docker', 'exec', 'homehub', 'python', '/app/app.py', 'sync-catalog']
        if publish_existing: args.append('--publish-existing')
        run(*args)
        health(public_url, m['version'])
        atomic(ROOT / 'releases/current.json', json.dumps({'revision': m['revision'], 'version': m['version'], 'image': image, 'rollback_backup': name}).encode())
    except Exception:
        print('Deployment failed; attempting automatic code/image rollback', flush=True)
        restore(name, public_url)
        raise
    print('Release healthy: ' + m['version'] + ' ' + m['revision'], flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=('deploy', 'rollback')); p.add_argument('target')
    p.add_argument('--public-url', required=True); p.add_argument('--publish-existing', action='store_true')
    a = p.parse_args()
    # Protect backup files independent of runner/SSH defaults.
    os.umask(0o077)
    with lock():
        if a.command == 'deploy': deploy(a.target, a.public_url, a.publish_existing)
        else:
            backup()  # preserve the current code and data before manual code rollback
            restore(a.target, a.public_url)


if __name__ == '__main__':
    main()
