"""macOS CI check: a real XRD Plotter 0.0.5 app installs the XRD-compatible Lab Plot Toolbox package with its own updater.

Downloads the published XRD Plotter 0.0.5 DMG for this architecture, then does exactly what XRD Plotter does after
downloading an update: copies itself as "XRD Updater.app" and runs `--apply-update job.json`. The helper's trusted
public key is swapped for a throwaway test key (the real XRD Plotter key never leaves the publisher's computer).

Run after `python packaging/build.py --xrd-compat`.
"""
import base64
import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from labstudio.paths import APP_VERSION  # noqa: E402

arch = platform.machine()
dmg_name = {'arm64': 'XRD_Plotter_0.0.5_Mac_AppleSilicon.dmg', 'x86_64': 'XRD_Plotter_0.0.5_Mac_Intel.dmg'}[arch]
package_src = next((ROOT / 'release').glob(f'LabPlotToolbox-{APP_VERSION}-macOS-{arch}-XRD-Plotter-update.zip'))
work = Path(tempfile.mkdtemp(prefix='xrd-mac-migration-'))
print(f'Downloading {dmg_name}', flush=True)
dmg = work / dmg_name
urllib.request.urlretrieve(f'https://github.com/Greenbean0123/xrd-plotter/releases/download/v0.0.5/{dmg_name}', dmg)
mount = work / 'mnt'
mount.mkdir()
subprocess.run(['hdiutil', 'attach', '-nobrowse', '-readonly', '-mountpoint', str(mount), str(dmg)], check=True,
               stdout=subprocess.DEVNULL)
apps = work / 'Applications'
apps.mkdir()
target = apps / 'XRD Plotter.app'
try:
    subprocess.run(['ditto', str(mount / 'XRD Plotter.app'), str(target)], check=True)
finally:
    subprocess.run(['hdiutil', 'detach', str(mount)], check=False, stdout=subprocess.DEVNULL)
old_version = plistlib.loads((target / 'Contents' / 'Info.plist').read_bytes())['CFBundleShortVersionString']
print('Installed XRD Plotter', old_version, flush=True)

# The job lives where XRD Plotter keeps it, so Lab Plot Toolbox recognises its ready signal and later cleans up.
job_dir = Path.home() / 'Library' / 'Application Support' / 'XRD Plotter' / 'updates' / uuid.uuid4().hex
job_dir.mkdir(parents=True)
package = job_dir / 'package.zip'
shutil.copy2(package_src, package)
helper_app = job_dir / 'helper' / 'XRD Updater.app'
shutil.copytree(target, helper_app, symlinks=True)
key = Ed25519PrivateKey.generate()
public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
patched = 0
for config in helper_app.rglob('update_config.json'):
    if config.is_symlink():
        continue
    data = json.loads(config.read_text(encoding='utf-8'))
    data['public_key'] = public
    config.write_text(json.dumps(data), encoding='utf-8')
    patched += 1
assert patched, 'update_config.json not found in XRD Plotter.app'
subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(helper_app)], check=True)

size, digest = package.stat().st_size, hashlib.sha256(package.read_bytes()).hexdigest()
payload = {'schema': 1, 'version': APP_VERSION, 'notes': 'CI migration test', 'assets': {f'macos-{arch}': {
    'url': 'https://example.invalid/update.zip', 'size': size, 'sha256': digest, 'format': 'app-zip'}}}
canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
envelope = {'payload': payload, 'signature': base64.b64encode(key.sign(canonical)).decode()}
release = {'version': APP_VERSION, 'notes': payload['notes'], 'platform': f'macos-{arch}',
           'asset': payload['assets'][f'macos-{arch}'], 'envelope': envelope}
running_app = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4)'])  # stands in for the open XRD Plotter
job = {'target': str(target), 'package': str(package), 'release': release, 'parent_pid': running_app.pid,
       'parent_bootloader_pid': running_app.pid, 'ready': str(job_dir / 'helper-ready'),
       'health': str(job_dir / 'new-app-ready'), 'result': str(job_dir / 'result.json')}
(job_dir / 'job.json').write_text(json.dumps(job), encoding='utf-8')
env = dict(os.environ, LABSTUDIO_NO_UPDATE_CHECK='1', PYINSTALLER_RESET_ENVIRONMENT='1')
helper = subprocess.Popen([str(helper_app / 'Contents' / 'MacOS' / 'XRD Plotter'), '--apply-update', str(job_dir / 'job.json')],
                          cwd=job_dir, env=env)
t0, steps = time.time(), {}
while time.time() - t0 < 240:
    if 'ready' not in steps and (job_dir / 'helper-ready').exists():
        steps['ready'] = round(time.time() - t0, 1)
        (job_dir / 'install-approved').write_text('approved')
    if 'health' not in steps and (job_dir / 'new-app-ready').exists():
        steps['health'] = round(time.time() - t0, 1)
    if (job_dir / 'result.json').exists() and helper.poll() is not None:
        break
    time.sleep(0.25)
result = json.loads((job_dir / 'result.json').read_text(encoding='utf-8')) if (job_dir / 'result.json').exists() else {}
log = job_dir / 'installer.log'
info = plistlib.loads((target / 'Contents' / 'Info.plist').read_bytes())
print('steps (s):', steps, '| helper exit', helper.poll(), '| result:', {k: v for k, v in result.items() if k != 'backup'})
print('installed app:', info.get('CFBundleIdentifier'), info.get('CFBundleShortVersionString'))
migrated = info.get('CFBundleShortVersionString') == APP_VERSION and (target / 'Contents' / 'Resources' / 'labstudio').exists()
deadline = time.time() + 60
while time.time() < deadline and (list(apps.glob('*.previous-*')) or job_dir.exists()):
    time.sleep(1)
cleaned = not list(apps.glob('*.previous-*')) and not job_dir.exists()
print('backup and transaction cleaned up:', cleaned)
subprocess.run(['pkill', '-f', str(target)], check=False)
ok = bool(result.get('success')) and migrated and 'health' in steps and cleaned
if not ok and log.exists():
    print(log.read_text(errors='replace')[-4000:])
print('XRD PLOTTER MAC MIGRATION', 'PASSED' if ok else 'FAILED')
sys.exit(0 if ok else 1)
