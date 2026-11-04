"""Updater checks: signatures, tampering, platform rules, ZIP safety, and the swap / rollback transaction.

Run:  .venv\\Scripts\\python.exe tests\\test_updates.py
"""
import base64
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from labstudio.update import installer, updater  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  ({detail})' if detail else ''))


def raises(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except Exception as error:
        return str(error)
    return None


key = Ed25519PrivateKey.generate()
public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
asset = {'url': 'https://example.org/app.exe', 'size': 10, 'sha256': 'a' * 64, 'format': 'exe'}
mac = {'url': 'https://example.org/app.zip', 'size': 10, 'sha256': 'b' * 64, 'format': 'app-zip'}


def signed(payload, signer=key):
    return {'payload': payload, 'signature': base64.b64encode(signer.sign(updater.canonical(payload))).decode()}


payload = {'schema': 1, 'version': '0.2.0', 'notes': 'n', 'assets': {'windows-x86_64': asset, 'macos-arm64': mac}}
envelope = signed(payload)
release = updater.verify_manifest(envelope, public, '0.1.0', key='windows-x86_64')
check('valid signed manifest accepted', release and release['version'] == '0.2.0')
check('same or older version is not offered', updater.verify_manifest(envelope, public, '0.2.0', key='windows-x86_64') is None
      and updater.verify_manifest(envelope, public, '1.0.0', key='windows-x86_64') is None)
tampered = json.loads(json.dumps(envelope))
tampered['payload']['assets']['windows-x86_64']['url'] = 'https://evil.example/app.exe'
check('tampered manifest rejected', 'signature' in (raises(updater.verify_manifest, tampered, public, '0.1.0', key='windows-x86_64') or ''))
check('manifest signed by another key rejected',
      raises(updater.verify_manifest, signed(payload, Ed25519PrivateKey.generate()), public, '0.1.0', key='windows-x86_64') is not None)
check('missing platform rejected', 'macos-x86_64' in (raises(updater.verify_manifest, envelope, public, '0.1.0', key='macos-x86_64') or ''))
bad = dict(payload, assets={'windows-x86_64': dict(asset, url='http://example.org/app.exe')})
check('plain HTTP download rejected', raises(updater.verify_manifest, signed(bad), public, '0.1.0', key='windows-x86_64') is not None)
bad = dict(payload, assets={'windows-x86_64': dict(asset, format='app-zip')})
check('wrong package format rejected', raises(updater.verify_manifest, signed(bad), public, '0.1.0', key='windows-x86_64') is not None)
check('bundled config has a key and the release feed', updater.bundled_config()['public_key'] and
      updater.bundled_config()['manifest_url'].startswith('https://github.com/Greenbean0123/lab-plot-toolbox/'))
check('version format enforced', raises(updater.version_tuple, '1.2') is not None and updater.version_tuple('0.10.2') == (0, 10, 2))

with tempfile.TemporaryDirectory() as folder:
    folder = Path(folder)
    # ZIP safety
    def make_zip(name, entries):
        path = folder / name
        with zipfile.ZipFile(path, 'w') as archive:
            for entry, data in entries:
                archive.writestr(entry, data)
        return path
    ok = make_zip('ok.zip', [(f'{updater.APP_BUNDLE}/Contents/Info.plist', 'x')])
    check('well-formed app ZIP accepted', raises(installer.validate_zip, ok) is None)
    check('ZIP with ../ rejected', raises(installer.validate_zip, make_zip('a.zip', [(f'{updater.APP_BUNDLE}/../x', 'x')])) is not None)
    check('ZIP outside the app bundle rejected', raises(installer.validate_zip, make_zip('b.zip', [('Other.app/x', 'x')])) is not None)
    link = zipfile.ZipInfo(f'{updater.APP_BUNDLE}/Contents/escape')
    link.external_attr = (0o120777 << 16)
    path = folder / 'link.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(link, '../../../etc')
    check('symlink escaping the bundle rejected', raises(installer.validate_zip, path) is not None)
    # Windows executable check
    check('non-executable package rejected', raises(installer.check_windows_executable, ok) is not None)
    check('this Python is accepted as an x64 executable', raises(installer.check_windows_executable, sys.executable) is None
          if sys.platform == 'win32' else True)

    # Swap and rollback transaction with a fake launcher
    class Fake:
        def __init__(self, exits=False):
            self.exits = exits

        def poll(self):
            return 1 if self.exits else None

    target = folder / 'App.exe'
    target.write_text('old')
    staged = folder / '.staged.exe'
    staged.write_text('new')
    health = folder / 'ready'

    def healthy(path, health_file=None):
        if health_file:
            health_file.write_text('ready')
        return Fake()
    backup = installer.replace_and_restart(target, staged, health, launch_app=healthy, timeout=5)
    check('update swaps the app and keeps a backup', target.read_text() == 'new' and backup.read_text() == 'old')
    backup.unlink()
    staged.write_text('broken')
    health.unlink()
    launched = []

    def broken(path, health_file=None):
        launched.append(path.read_text())
        return Fake(exits=health_file is not None)
    error = raises(installer.replace_and_restart, target, staged, health, launch_app=broken, timeout=5)
    check('failed update is rolled back and the old app reopened', error and target.read_text() == 'new' and launched == ['broken', 'new'],
          error)

# Health signal: our own installer and XRD Plotter's installer (migration), only inside the expected folders.
import os  # noqa: E402
with tempfile.TemporaryDirectory() as folder:
    folder = Path(folder)
    updater.legacy_xrd_directory = lambda: folder / 'XRD Plotter'
    os.environ['LABSTUDIO_DATA_DIR'] = str(folder / 'LabPlotStudio')
    own = folder / 'LabPlotStudio' / 'updates' / ('a' * 32) / 'new-app-ready'
    legacy = folder / 'XRD Plotter' / 'updates' / ('b' * 32) / 'new-app-ready'
    stray = folder / 'elsewhere' / 'x' / 'new-app-ready'
    for path in (own, legacy, stray):
        path.parent.mkdir(parents=True)
    os.environ[updater.HEALTH_ENV], os.environ[updater.LEGACY_HEALTH_ENV] = str(own), str(legacy)
    updater.mark_ready()
    check('ready signal answered for our installer and for XRD Plotter migration',
          own.read_text() == 'ready' and legacy.read_text() == 'ready')
    os.environ[updater.LEGACY_HEALTH_ENV] = str(stray)
    updater.mark_ready()
    check('ready signal outside the update folders ignored', not stray.exists())

check('hash helper matches hashlib', updater.file_sha256(ROOT / 'LICENSE') == __import__('hashlib').sha256((ROOT / 'LICENSE').read_bytes()).hexdigest())
print(f'\n{sum(results)}/{len(results)} passed')
sys.exit(0 if all(results) else 1)
