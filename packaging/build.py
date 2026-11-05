"""Build the desktop package on the current machine (Windows or macOS) and self-test it.

    python packaging/build.py            # Windows: single .exe   macOS: .app -> .zip and .dmg
    python packaging/build.py --onedir   # Windows: folder build (faster start-up), zipped
    python packaging/build.py --xrd-compat   # macOS: Lab Plot Toolbox packaged as "XRD Plotter.app" (see below)

The --xrd-compat package lets installed XRD Plotter apps on macOS update themselves to Lab Plot Toolbox: XRD Plotter's
installer only accepts a ZIP holding "XRD Plotter.app" with bundle id org.xrdplotter.app and the release version.

Results go to release/. The packaged app is run once with --self-test; the build fails if that fails.
"""
import json
import os
import platform
import plistlib
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = 'Lab Plot Toolbox'
BUNDLE_ID = 'io.github.greenbean0123.labplotstudio'  # unchanged since 0.1.2, so installs keep updating
XRD_NAME, XRD_BUNDLE_ID = 'XRD Plotter', 'org.xrdplotter.app'
VERSION = runpy.run_path(str(ROOT / 'labstudio' / 'paths.py'))['APP_VERSION']
SYSTEM = platform.system()
ARCH = {'amd64': 'x64', 'x86_64': 'x64' if SYSTEM == 'Windows' else 'x86_64', 'arm64': 'arm64'}.get(platform.machine().lower(),
                                                                                                    platform.machine())
RELEASE = Path(os.environ.get('LABSTUDIO_RELEASE_DIR', ROOT / 'release'))  # override for test builds
WORK = ROOT / 'build' / 'pyinstaller'

HIDDEN = ['labstudio.xrd.view', 'labstudio.tem.view', 'labstudio.uvvis.view', 'labstudio.nmr.view',  # loaded by name on first use
          'labstudio.update.manager', 'labstudio.update.installer', 'labstudio.update.cleanup',
          'labstudio.settings', 'labstudio.selftest', 'PIL._tkinter_finder',
          'matplotlib.backends.backend_tkagg', 'matplotlib.backends.backend_agg',
          'matplotlib.backends.backend_pdf', 'matplotlib.backends.backend_svg']
EXCLUDE = ['scipy', 'pytest', 'IPython', 'notebook', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'pandas',
           'matplotlib.tests', 'numpy.tests', 'PIL.ImageQt', 'tkinter.test']


def run(cmd, **kwargs):
    print('>', ' '.join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT, **kwargs)


def pyinstaller(onefile, name=NAME, bundle_id=BUNDLE_ID):
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--name', name,
            '--distpath', ROOT / 'dist', '--workpath', WORK, '--specpath', WORK,
            '--add-data', f'{ROOT / "labstudio" / "assets"}:labstudio/assets',
            '--add-data', f'{ROOT / "demo-data"}:demo-data',
            '--add-data', f'{ROOT / "labstudio" / "update_config.json"}:labstudio']
    for module in HIDDEN:
        args += ['--hidden-import', module]
    for module in EXCLUDE:
        args += ['--exclude-module', module]
    if SYSTEM == 'Windows':
        args += ['--icon', ROOT / 'labstudio' / 'assets' / 'app.ico', '--version-file', version_file()]
        args += ['--onefile'] if onefile else ['--onedir']
    else:
        args += ['--icon', ROOT / 'labstudio' / 'assets' / 'app.icns', '--onedir',
                 '--osx-bundle-identifier', bundle_id]
    run(args + [ROOT / 'main.py'])


def version_file():
    numbers = tuple(int(v) for v in VERSION.split('.')) + (0,)
    path = WORK / 'windows-version.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Greenbean0123'),
    StringStruct('FileDescription', '{NAME}'),
    StringStruct('FileVersion', '{VERSION}'),
    StringStruct('ProductName', '{NAME}'),
    StringStruct('ProductVersion', '{VERSION}'),
    StringStruct('LegalCopyright', 'MIT License')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding='utf-8')
    return path


def self_test(executable):
    report = ROOT / 'build' / f'selftest-{SYSTEM.lower()}-{ARCH}.json'
    report.unlink(missing_ok=True)
    print(f'> self-test {executable}', flush=True)
    result = subprocess.run([str(executable), '--self-test', str(report)], timeout=600)
    data = json.loads(report.read_text(encoding='utf-8')) if report.is_file() else {'success': False, 'error': 'no report'}
    for check in data.get('checks', []):
        print(('  ok   ' if check['ok'] else '  FAIL ') + check['name'])
    if result.returncode != 0 or not data.get('success'):
        raise SystemExit(f"Packaged self-test failed: {data.get('error')}\n{data.get('traceback', '')}")
    print(f"  {len(data['checks'])} checks passed in the packaged app", flush=True)


def size_mb(path):
    path = Path(path)
    total = path.stat().st_size if path.is_file() else sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
    return total / 1024 ** 2


def build_windows(onefile):
    pyinstaller(onefile)
    RELEASE.mkdir(exist_ok=True)
    if onefile:
        exe = ROOT / 'dist' / f'{NAME}.exe'
        self_test(exe)
        target = RELEASE / f'LabPlotToolbox-{VERSION}-Windows-{ARCH}.exe'
        shutil.copy2(exe, target)
    else:
        folder = ROOT / 'dist' / NAME
        self_test(folder / f'{NAME}.exe')
        target = Path(shutil.make_archive(str(RELEASE / f'LabPlotToolbox-{VERSION}-Windows-{ARCH}'), 'zip', folder.parent, NAME))
    print(f'Built {target.name}: {size_mb(target):.1f} MB', flush=True)


def build_mac(xrd_compat=False):
    name, bundle_id = (XRD_NAME, XRD_BUNDLE_ID) if xrd_compat else (NAME, BUNDLE_ID)
    pyinstaller(onefile=False, name=name, bundle_id=bundle_id)
    app = ROOT / 'dist' / f'{name}.app'
    plist = app / 'Contents' / 'Info.plist'
    info = plistlib.loads(plist.read_bytes())
    info.update(CFBundleShortVersionString=VERSION, CFBundleVersion=VERSION, CFBundleDisplayName=name,
                NSHighResolutionCapable=True, LSMinimumSystemVersion='11.0', NSHumanReadableCopyright='MIT License')
    plist.write_bytes(plistlib.dumps(info))
    run(['codesign', '--force', '--deep', '--sign', '-', app])  # ad-hoc signature (not notarized)
    self_test(app / 'Contents' / 'MacOS' / name)
    RELEASE.mkdir(exist_ok=True)
    if xrd_compat:
        path = RELEASE / f'LabPlotToolbox-{VERSION}-macOS-{ARCH}-XRD-Plotter-update.zip'
        path.unlink(missing_ok=True)
        run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, path])
        print(f'Built {path.name}: {size_mb(path):.1f} MB', flush=True)
        return
    stem = f'LabPlotToolbox-{VERSION}-macOS-{ARCH}'
    zip_path = RELEASE / f'{stem}.zip'
    zip_path.unlink(missing_ok=True)
    run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, zip_path])
    stage = Path(tempfile.mkdtemp(prefix='dmg-'))
    shutil.copytree(app, stage / app.name, symlinks=True)
    (stage / 'Applications').symlink_to('/Applications')
    dmg = RELEASE / f'{stem}.dmg'
    dmg.unlink(missing_ok=True)
    run(['hdiutil', 'create', '-volname', NAME, '-srcfolder', stage, '-ov', '-format', 'UDZO', dmg])
    for path in (zip_path, dmg):
        print(f'Built {path.name}: {size_mb(path):.1f} MB', flush=True)


if __name__ == '__main__':
    if SYSTEM == 'Windows':
        build_windows(onefile='--onedir' not in sys.argv)
    elif SYSTEM == 'Darwin':
        build_mac(xrd_compat='--xrd-compat' in sys.argv)
    else:
        raise SystemExit('Build on Windows or macOS.')
