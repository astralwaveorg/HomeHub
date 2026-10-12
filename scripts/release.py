#!/usr/bin/env python3
"""Homehub release client. No network calls unless deploy/rollback omit --dry-run."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10']
RUNTIME = {'app.py', 'catalog.py', 'seed.json', 'ambient.wav', 'Dockerfile', 'compose.yaml', 'VERSION', '.dockerignore', 'README.md', 'CHANGELOG.md'}


def allowed(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or any(x.startswith('.') for x in p.parts if x not in ('.dockerignore', '.github')):
        return False
    if any(x in ('data', 'screenshots', '__pycache__', 'node_modules', 'releases', 'backups') for x in p.parts):
        return False
    return name in RUNTIME or name == 'README.md' or p.parts[0] in ('static', 'scripts', 'tests', 'docs', '.github')


def git(*args, root=ROOT):
    return subprocess.check_output(['git', *args], cwd=root)


def package(output, root=ROOT):
    if git('status', '--porcelain', '--untracked-files=all', root=root).strip():
        raise ValueError('Release requires a CLEAN committed checkout, including no untracked files')
    revision = git('rev-parse', 'HEAD', root=root).decode().strip()
    version = git('show', 'HEAD:VERSION', root=root).decode().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION must be numeric major.minor.patch')
    files = [x for x in git('ls-tree', '-r', '--name-only', 'HEAD', root=root).decode().splitlines() if allowed(x)]
    if not RUNTIME.issubset(files):
        raise ValueError('Required runtime files missing from commit')
    archive = git('archive', '--format=tar', 'HEAD', '--', *sorted(files), root=root)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
        for m in tf.getmembers():
            if not (m.isfile() or m.isdir()):
                raise ValueError('Symlinks and special files are forbidden in releases')
    output = Path(output).resolve()
    if output == root or root in output.parents:
        raise ValueError('Package output must be outside repository (keeps checkout clean)')
    output.mkdir(parents=True, exist_ok=True)
    name = f'homehub-{version}-{revision}.tar'
    manifest = {'version': version, 'revision': revision, 'archive': name,
                'sha256': hashlib.sha256(archive).hexdigest(), 'files': sorted(files)}
    (output / name).write_bytes(archive)
    path = output / (name + '.json')
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print(path)
    return path


# Only fixed transport names are extracted; executor comes from the checksummed archive.
BOOTSTRAP = '''import io,json,hashlib,pathlib,subprocess,sys,tarfile
root=pathlib.Path('/vol1/1000/docker/homehub')
if root.resolve()!=root: raise SystemExit('Unsafe root')
buf=sys.stdin.buffer.read()
with tarfile.open(fileobj=io.BytesIO(buf)) as t:
 if sorted(t.getnames())!=['archive.tar','manifest.json']: raise SystemExit('Invalid transport')
 m=json.load(t.extractfile('manifest.json')); a=t.extractfile('archive.tar').read()
import re
if not re.fullmatch('[0-9a-f]{40}',m['revision']) or hashlib.sha256(a).hexdigest()!=m['sha256']: raise SystemExit('Integrity failure')
stage=root/'releases'/m['revision']
if (root/'releases').is_symlink() or stage.is_symlink(): raise SystemExit('Unsafe stage')
stage.mkdir(parents=True,exist_ok=True)
for n,b in [('archive.tar',a),('manifest.json',json.dumps(m).encode())]:
 p=stage/n
 if p.is_symlink(): raise SystemExit('Unsafe stage file')
 if p.exists() and p.read_bytes()!=b: raise SystemExit('Immutable stage conflict')
 p.write_bytes(b)
with tarfile.open(fileobj=io.BytesIO(a)) as t:
 p=t.getmember('scripts/deploy_remote.py')
 if not p.isfile(): raise SystemExit('Invalid executor')
 executor=t.extractfile(p).read()
subprocess.run([sys.executable,'-c',executor.decode(),'deploy',str(stage),*sys.argv[1:]],check=True)
'''


def transport(manifest_path):
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    if Path(manifest['archive']).name != manifest['archive']:
        raise ValueError('Invalid archive filename')
    archive = path.with_name(manifest['archive']).read_bytes()
    if hashlib.sha256(archive).hexdigest() != manifest['sha256']:
        raise ValueError('Artifact SHA256 mismatch')
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as t:
        for name, data in [('archive.tar', archive), ('manifest.json', path.read_bytes())]:
            info = tarfile.TarInfo(name); info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue(), manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    pk = sub.add_parser('package'); pk.add_argument('--output', required=True)
    for cmd in ('deploy', 'rollback'):
        q = sub.add_parser(cmd)
        q.add_argument('--host', default='root@10.0.1.2')
        q.add_argument('--public-url', default='https://homehub.stream:8443')
        q.add_argument('--dry-run', action='store_true')
        if cmd == 'deploy':
            q.add_argument('--manifest', required=True)
            q.add_argument('--publish-existing', action='store_true', help='Approved one-time catalog visibility migration')
        else:
            q.add_argument('--backup', required=True, help='Backup basename printed by deploy; never restores data')
    a = p.parse_args()
    if a.command == 'check':
        subprocess.run([sys.executable, str(ROOT / 'scripts/check.py')], check=True)
    elif a.command == 'package':
        subprocess.run([sys.executable, str(ROOT / 'scripts/check.py')], check=True)
        package(a.output)
    else:
        if not re.fullmatch(r'[a-zA-Z0-9_.@:-]+', a.host) or a.host.startswith('-'):
            p.error('Invalid SSH host')
        extra = ['--public-url', a.public_url]
        if a.command == 'deploy':
            data, manifest = transport(a.manifest)
            if a.publish_existing: extra.append('--publish-existing')
            command = ['python3', '-c', BOOTSTRAP, *extra]
            print(f"Deploy {manifest['version']} {manifest['revision']} to {a.host}; fixed Homehub root only", flush=True)
        else:
            if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', a.backup):
                p.error('Invalid backup basename')
            data = b''
            executor = (ROOT / 'scripts/deploy_remote.py').read_text()
            command = ['python3', '-c', executor, 'rollback', a.backup, *extra]
            print(f'Code/image rollback using {a.backup}; live data retained', flush=True)
        if a.dry_run:
            print('DRY RUN: no SSH, upload, build, backup or runtime changes')
            return
        subprocess.run([*SSH, a.host, shlex.join(command)], input=data, check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as e:
        sys.exit(str(e))
