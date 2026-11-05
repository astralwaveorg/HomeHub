"""Release safety tests: temporary Git repositories only, no Docker/SSH required."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


release = load('release')
remote = load('deploy_remote')


class ReleaseTests(unittest.TestCase):
    def test_allowlist(self):
        for name in ('data/homehub.db', '.env', '.git/config', 'tests/screenshots/a.png', '../app.py', '/app.py', 'static/../../data/x'):
            self.assertFalse(release.allowed(name), name)
            self.assertFalse(remote.runtime(name), name)
        for name in ('app.py', 'collector.py', 'VERSION', 'static/app.js'):
            self.assertTrue(release.allowed(name)); self.assertTrue(remote.runtime(name))
        self.assertFalse(remote.runtime('scripts/release.py'))

    def test_compose_pin(self):
        single = b'name: homehub\nservices:\n  homehub:\n    image: homehub-portal:1.0.0\n'
        pinned = remote.pin_compose(single, 'homehub-portal:retained-123456789abcdef0')
        self.assertEqual(pinned.count(b'${HOMEHUB_IMAGE:-homehub-portal:retained-'), 1)
        twin = b'name: homehub\nservices:\n  homehub:\n    image: a\n  collector:\n    image: b\n'
        pinned = remote.pin_compose(twin, 'homehub-portal:retained-123456789abcdef0')
        self.assertEqual(pinned.count(b'${HOMEHUB_IMAGE:-homehub-portal:retained-'), 2)
        with self.assertRaises(ValueError): remote.pin_compose(b'no image', 'x')

    def test_deploy_requires_both_services(self):
        one = b'name: homehub\nservices:\n  homehub:\n    image: a\n'
        two = b'name: homehub\nservices:\n  homehub:\n    image: a\n  collector:\n    image: b\n'
        import re as re_module
        self.assertEqual(len(re_module.findall(r'(?m)^    image:', two.decode())), 2)
        self.assertNotEqual(len(re_module.findall(r'(?m)^    image:', one.decode())), 2)

    def test_safe_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'homehub'; root.mkdir()
            with patch.object(remote, 'ROOT', root):
                self.assertEqual(remote.safe(root / 'data/homehub.db'), root / 'data/homehub.db')
                with self.assertRaises(ValueError): remote.safe(Path(tmp) / 'other')
                (root / 'static').symlink_to(Path(tmp))
                with self.assertRaises(ValueError): remote.safe(root / 'static/app.js')

    def test_archive_reproducible_clean_and_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'; root.mkdir()
            out = Path(tmp) / 'out'
            def git(*args):
                subprocess.run(['git', *args], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git('init', '-b', 'main')
            for name in release.RUNTIME:
                (root / name).write_text('1.1.0\n' if name == 'VERSION' else 'test\n')
            (root / 'data').mkdir(); (root / 'data/homehub.db').write_text('forbidden')
            (root / 'tests/screenshots').mkdir(parents=True)
            (root / 'tests/screenshots/a.png').write_bytes(b'not packaged')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'test')
            path = release.package(out, root)
            first = path.with_name(json.loads(path.read_text())['archive']).read_bytes()
            release.package(out, root)
            self.assertEqual(first, path.with_name(json.loads(path.read_text())['archive']).read_bytes())
            self.assertEqual(json.loads(path.read_text())['sha256'], hashlib.sha256(first).hexdigest())
            with tarfile.open(fileobj=io.BytesIO(first)) as tf:
                self.assertNotIn('data/homehub.db', tf.getnames())
                self.assertNotIn('tests/screenshots/a.png', tf.getnames())
            release.transport(path)
            result = subprocess.run(['python3', str(ROOT / 'scripts/release.py'), 'deploy', '--manifest', str(path), '--dry-run'], capture_output=True, text=True, check=True)
            self.assertIn('DRY RUN: no SSH', result.stdout)
            rollback = subprocess.run(['python3', str(ROOT / 'scripts/release.py'), 'rollback', '--backup', '20260101T010101Z-12345678', '--dry-run'], capture_output=True, text=True, check=True)
            self.assertIn('live data retained', rollback.stdout)
            path.with_name(json.loads(path.read_text())['archive']).write_bytes(b'corrupted')
            with self.assertRaises(ValueError): release.transport(path)
            (root / 'app.py').write_text('dirty')
            with self.assertRaises(ValueError): release.package(out, root)

    def test_symlink_archive_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'; root.mkdir()
            def git(*args):
                subprocess.run(['git', *args], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git('init')
            for name in release.RUNTIME:
                (root / name).write_text('1.1.0\n' if name == 'VERSION' else 'x')
            (root / 'static').mkdir(); (root / 'static/leak').symlink_to('/etc/passwd')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'test')
            with self.assertRaises(ValueError): release.package(Path(tmp) / 'out', root)


class RemoteTransactionTests(unittest.TestCase):
    def test_online_backup_and_exclusive_lock(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'homehub'; (root / 'data/media').mkdir(parents=True)
            (root / 'app.py').write_text('old source')
            (root / 'data/media/music.wav').write_bytes(b'fixture audio')
            (root / 'data/initial-admin-password').write_text('fixture-not-for-backup')
            with sqlite3.connect(root / 'data/homehub.db') as c:
                c.execute('PRAGMA journal_mode=WAL')
                c.execute('CREATE TABLE fixture(value TEXT)')
                c.execute("INSERT INTO fixture VALUES('preserve')"); c.commit()
                with patch.object(remote, 'ROOT', root), patch.object(remote, 'current_image', return_value=('sha256:' + 'a' * 64, 'homehub-portal:retained-' + 'a' * 16)):
                    with remote.lock():
                        with self.assertRaises(RuntimeError):
                            with remote.lock(): pass
                        name, _ = remote.backup()
            dest = root / 'backups' / name
            with sqlite3.connect(dest / 'homehub.db') as db:
                self.assertEqual(db.execute('SELECT value FROM fixture').fetchone()[0], 'preserve')
            self.assertEqual((dest / 'media/music.wav').read_bytes(), b'fixture audio')
            self.assertFalse((dest / 'initial-admin-password').exists())
            self.assertEqual(dest.stat().st_mode & 0o777, 0o700)

    def test_original_image_rollback_preserves_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'homehub'; root.mkdir()
            name = '20260101T010101Z-12345678'
            dest = root / 'backups' / name; (dest / 'source').mkdir(parents=True)
            image_id = 'sha256:' + 'a' * 64
            image = 'homehub-portal:retained-' + 'a' * 16
            (dest / 'runtime.json').write_text(json.dumps({'image': image, 'image_id': image_id, 'source_files': ['app.py', 'compose.yaml']}))
            (dest / 'source/app.py').write_text('original 1.0.0 code')
            (dest / 'source/compose.yaml').write_text('services:\n  homehub:\n    image: homehub-portal:1.0.0\n')
            (root / 'data').mkdir(); (root / 'data/homehub.db').write_bytes(b'live data with sessions')
            (dest / 'homehub.db').write_bytes(b'old backup MUST NOT restore')
            with patch.object(remote, 'ROOT', root), patch.object(remote, 'run', return_value=image_id), patch.object(remote, 'compose') as compose, patch.object(remote, 'health'):
                remote.restore(name, 'https://example.com')
            compose.assert_called_once_with(image)
            self.assertEqual((root / 'data/homehub.db').read_bytes(), b'live data with sessions')
            self.assertEqual((root / 'app.py').read_text(), 'original 1.0.0 code')
            self.assertIn(image, (root / 'compose.yaml').read_text())

    def test_failure_rolls_back_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'homehub'; root.mkdir()
            revision = 'b' * 40; stage = root / 'releases' / revision; stage.mkdir(parents=True)
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w') as tf:
                for name in remote.RUNTIME:
                    data = (b'services:\n  homehub:\n    image: test\n  collector:\n    image: test\n'
                            if name == 'compose.yaml' else b'new runtime')
                    info = tarfile.TarInfo(name); info.size = len(data); tf.addfile(info, io.BytesIO(data))
            payload = archive.getvalue(); (stage / 'archive.tar').write_bytes(payload)
            (stage / 'manifest.json').write_text(json.dumps({'revision': revision, 'version': '1.1.0', 'sha256': hashlib.sha256(payload).hexdigest()}))
            calls = []
            def run(*args, **kwargs):
                calls.append(args)
                if args[:3] == ('docker', 'image', 'inspect'): return revision
            with patch.object(remote, 'ROOT', root), patch.object(remote, 'run', side_effect=run), patch.object(remote, 'backup', return_value=('20260101T010101Z-12345678', {})), patch.object(remote, 'compose'), patch.object(remote, 'health', side_effect=RuntimeError('unhealthy')), patch.object(remote, 'restore') as restore:
                with self.assertRaises(RuntimeError): remote.deploy(stage, 'https://example.com')
                restore.assert_called_once_with('20260101T010101Z-12345678', 'https://example.com')
            self.assertIn(('docker', 'stop', 'homehub'), calls)
            self.assertEqual((stage / 'source/app.py').stat().st_mode & 0o777, 0o644)


if __name__ == '__main__':
    unittest.main()
