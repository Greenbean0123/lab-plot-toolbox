"""Re-sign XRD Plotter's update feed so Windows AND macOS XRD Plotter installs move to Lab Plot Toolbox <version>.

Windows gets the normal Lab Plot Toolbox exe (checked against the signed Lab Plot Toolbox feed); macOS gets the
XRD-compatible zips (an "XRD Plotter.app" bundle with XRD's bundle id, which XRD's own installer accepts).
Everything is verified with XRD Plotter's own updater code before uploading.
"""
import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization

VERSION = sys.argv[1]
GH = r'C:\Program Files\GitHub CLI\gh.exe'
NEW_REPO, OLD_REPO = 'Greenbean0123/lab-plot-toolbox', 'Greenbean0123/xrd-plotter'
RELEASE_TAG = 'lab-plot-studio-0.1.2'  # existing migration release on the old repo
XRD = Path(r'Y:\AI\ChatGPT\XRD软件 4')
work = Path(tempfile.mkdtemp(prefix='xrd-feed-'))
files = {
    'windows-x86_64': (f'LabPlotToolbox-{VERSION}-Windows-x64.exe', 'exe'),
    'macos-arm64': (f'LabPlotToolbox-{VERSION}-macOS-arm64-XRD-Plotter-update.zip', 'app-zip'),
    'macos-x86_64': (f'LabPlotToolbox-{VERSION}-macOS-x86_64-XRD-Plotter-update.zip', 'app-zip'),
}
args = [GH, 'release', 'download', f'v{VERSION}', '--repo', NEW_REPO, '--dir', str(work), '--pattern', 'updates.json',
        '--pattern', 'SHA256SUMS.txt']
for name, _ in files.values():
    args += ['--pattern', name]
subprocess.run(args, check=True)

sums = dict(reversed(line.split(None, 1)) for line in (work / 'SHA256SUMS.txt').read_text().splitlines() if line.strip())
sums = {Path(k.strip().lstrip('*')).name: v for k, v in sums.items()}
ours = json.loads((work / 'updates.json').read_text(encoding='utf-8'))['payload']['assets']
assets = {}
for platform_key, (name, fmt) in files.items():
    path = work / name
    size, digest = path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()
    assert sums.get(name) == digest, f'{name} differs from the release SHA256SUMS'
    if platform_key == 'windows-x86_64':
        assert ours[platform_key]['sha256'] == digest, 'exe differs from the signed Lab Plot Toolbox feed'
    assets[platform_key] = {'url': f'https://github.com/{NEW_REPO}/releases/download/v{VERSION}/{name}',
                            'size': size, 'sha256': digest, 'format': fmt}

notes = (f'XRD Plotter is now Lab Plot Toolbox {VERSION} (XRD + TEM + UV-Vis in one app). '
         'Your startup data folder and settings stay where they are. '
         f'XRD Plotter 已并入 Lab Plot Toolbox {VERSION}（XRD、TEM、UV-Vis 三合一），启动数据文件夹和设置保持不变。')
payload = {'schema': 1, 'version': VERSION, 'notes': notes, 'assets': assets}
canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
key = serialization.load_pem_private_key((XRD / '.release-keys' / 'update-signing-key.pem').read_bytes(), None)
envelope = {'payload': payload, 'signature': base64.b64encode(key.sign(canonical)).decode()}

spec = importlib.util.spec_from_file_location('xrd_updater', XRD / 'src' / 'updater.py')
xrd_updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xrd_updater)
public = json.loads((XRD / 'src' / 'update_config.json').read_text(encoding='utf-8'))['public_key']
for platform_key in files:
    for installed in ('0.0.2', '0.0.3', '0.0.4', '0.0.5'):
        release = xrd_updater.verify_manifest(envelope, public, installed, key=platform_key)
        assert release and release['asset']['url'] == assets[platform_key]['url'], (platform_key, installed)
manifest = work / 'updates.json'
manifest.write_text(json.dumps(envelope, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

body = f"""## XRD Plotter → Lab Plot Toolbox {VERSION}

XRD Plotter has been merged into **[Lab Plot Toolbox](https://github.com/{NEW_REPO})** — XRD, TEM and UV-Vis plotting
in one app, now with XRD phase identification (multi-phase matching, (hkl) assignment, Scherrer size).

**Windows and macOS:** XRD Plotter offers this update by itself (or click *Check for Updates*). Your startup data
folder and settings keep loading. On macOS the app keeps the name *XRD Plotter* in Applications after the update.

---

XRD Plotter 已并入 **Lab Plot Toolbox**（XRD、TEM、UV-Vis 三合一，新增 XRD 物相匹配与谢乐晶粒尺寸）。
Windows 和 Mac 版都会在软件内自动提示更新。

<details><summary>Update feed</summary>

`updates.json` is signed with XRD Plotter's update key and points to:

""" + '\n'.join(f'- `{p}`: `{files[p][0]}` (SHA-256 `{a["sha256"]}`)' for p, a in assets.items()) + """
</details>
"""
notes_file = work / 'notes.md'
notes_file.write_text(body, encoding='utf-8')
subprocess.run([GH, 'release', 'upload', RELEASE_TAG, str(manifest), '--repo', OLD_REPO, '--clobber'], check=True)
subprocess.run([GH, 'release', 'edit', RELEASE_TAG, '--repo', OLD_REPO, '--title', 'XRD Plotter is now Lab Plot Toolbox',
                '--notes-file', str(notes_file), '--latest'], check=True)

live = work / 'live.json'
subprocess.run(['curl', '-sSfL', '-o', str(live), f'https://github.com/{OLD_REPO}/releases/latest/download/updates.json'],
               check=True)
for platform_key in files:
    release = xrd_updater.verify_manifest(json.loads(live.read_text(encoding='utf-8')), public, '0.0.5', key=platform_key)
    print(f'XRD Plotter 0.0.5 on {platform_key} now sees:', release['version'], release['asset']['url'])
