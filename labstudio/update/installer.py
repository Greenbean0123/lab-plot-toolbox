"""Detached, transactional installer run from a copy of the app (adapted from XRD Plotter).

Steps: verify the signed release and the downloaded package again -> prepare the replacement next to the app ->
wait for the running app to exit -> swap in the new app, keeping a backup -> start it and wait for its ready signal.
If the new app does not report ready within two minutes, it is stopped and the previous app is restored and reopened.
"""
import ctypes
import json
import os
import plistlib
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from . import updater


def process_alive(pid):
    if pid <= 0:
        return False
    if sys.platform == 'win32':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def validate_zip(package):
    """Reject absolute paths, '..', entries outside the app bundle and symlinks that escape it."""
    with zipfile.ZipFile(package) as archive:
        total, paths, links = 0, [], {}
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename:
                raise ValueError('Unsafe path in update ZIP.')
            if not path.parts or path.parts[0] not in (updater.APP_BUNDLE, '__MACOSX'):
                raise ValueError(f'Update ZIP must contain {updater.APP_BUNDLE}.')
            paths.append(path)
            total += item.file_size
            if total > 2 * updater.MAX_DOWNLOAD:
                raise ValueError('Unpacked update is too large.')
            if stat.S_ISLNK(item.external_attr >> 16):
                link = archive.read(item).decode('utf-8')
                if link.startswith('/') or '\\' in link:
                    raise ValueError('Unsafe symlink in update ZIP.')
                links[path.parts] = PurePosixPath(link).parts
        for path in paths:  # resolve the whole symlink graph, including links in parent folders
            if any(path.parts[:index] in links for index in range(1, len(path.parts))):
                raise ValueError('Update ZIP contains entries beneath a symlink directory.')
            pending, components, followed = list(path.parts), [], 0
            while pending:
                part = pending.pop(0)
                if part == '..':
                    if len(components) <= 1:
                        raise ValueError('Update symlink escapes its application bundle.')
                    components.pop()
                elif part != '.':
                    components.append(part)
                    key = tuple(components)
                    if key in links:
                        followed += 1
                        if followed > 40:
                            raise ValueError('Cyclic or excessively deep update symlinks.')
                        components.pop()
                        pending = list(links[key]) + pending
            if not components or components[0] != path.parts[0]:
                raise ValueError('Update symlink escapes its application bundle.')


def check_windows_executable(path):
    with Path(path).open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Update is not a Windows executable.')
        stream.seek(0x3c)
        stream.seek(struct.unpack('<I', stream.read(4))[0])
        if stream.read(4) != b'PE\x00\x00' or struct.unpack('<H', stream.read(2))[0] != 0x8664:
            raise ValueError('Update is not an x64 Windows executable.')


def prepare_replacement(package, target, release):
    staged = target.with_name(f'.labstudio-update-{uuid.uuid4().hex}{target.suffix}')
    if release['asset']['format'] == 'exe':
        check_windows_executable(package)
        shutil.copy2(package, staged)
        return staged
    validate_zip(package)
    extracted = package.parent / ('extract-' + uuid.uuid4().hex)
    extracted.mkdir()
    subprocess.run(['ditto', '-x', '-k', str(package), str(extracted)], check=True, timeout=120)
    app = extracted / updater.APP_BUNDLE
    info = plistlib.loads((app / 'Contents' / 'Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != updater.BUNDLE_ID or info.get('CFBundleShortVersionString') != release['version']:
        raise ValueError('The Mac application identity or version does not match the release.')
    executable = app / 'Contents' / 'MacOS' / info['CFBundleExecutable']
    expected_arch = release['platform'].split('-', 1)[1]
    if expected_arch not in subprocess.check_output(['lipo', '-archs', str(executable)], text=True).split():
        raise ValueError('The Mac application architecture does not match this computer.')
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True, timeout=60)
    shutil.copytree(app, staged, symlinks=True)
    return staged


def rename_with_retry(source, destination):
    deadline = time.monotonic() + 15
    while True:
        try:
            source.replace(destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def launch(target, health=None):
    executable = target / 'Contents' / 'MacOS' / 'Lab Plot Toolbox' if target.suffix == '.app' else target
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
    env.pop(updater.HEALTH_ENV, None)
    if health:
        env[updater.HEALTH_ENV] = str(health)
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {'start_new_session': True}
    return subprocess.Popen([str(executable)], cwd=target.parent, env=env, **kwargs)


def replace_and_restart(target, staged, health, launch_app=launch, timeout=120):
    backup = target.with_name(f'{target.stem}.previous-{uuid.uuid4().hex[:8]}{target.suffix}')
    rename_with_retry(target, backup)
    process = None
    try:
        rename_with_retry(staged, target)
        process = launch_app(target, health)
        deadline = time.monotonic() + timeout
        while not health.exists():
            if process.poll() is not None:
                raise RuntimeError('The updated application exited before it was ready.')
            if time.monotonic() >= deadline:
                raise RuntimeError('The updated application did not become ready in time.')
            time.sleep(0.2)
        return backup
    except Exception:
        if process is not None and process.poll() is None:
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if target.exists():
            rename_with_retry(target, target.with_name(f'{target.stem}.failed-{uuid.uuid4().hex[:8]}{target.suffix}'))
        rename_with_retry(backup, target)
        launch_app(target)
        raise


def run(job_path):
    job = json.loads(Path(job_path).read_text(encoding='utf-8'))
    result_path = Path(job['result'])
    try:
        release = updater.verify_manifest(job['release']['envelope'], updater.bundled_config()['public_key'],
                                          updater.APP_VERSION)
        if release is None:
            raise ValueError('The update must be newer than the current application.')
        package, target = Path(job['package']).resolve(), Path(job['target']).resolve()
        if package.stat().st_size != release['asset']['size'] or updater.file_sha256(package) != release['asset']['sha256'].lower():
            raise ValueError('The downloaded package changed after verification.')
        if target.suffix != ('.app' if sys.platform == 'darwin' else '.exe'):
            raise ValueError('Invalid application target.')
        staged = prepare_replacement(package, target, release)
        Path(job['ready']).write_text('ready')
        deadline = time.monotonic() + 90
        approval = Path(job['ready']).parent / 'install-approved'
        cancellation = Path(job['ready']).parent / 'cancel'
        pids = [int(job['parent_pid'])]
        if sys.platform == 'win32':
            pids.append(int(job['parent_bootloader_pid']))
        while not approval.exists() or any(process_alive(pid) for pid in pids):
            if cancellation.exists():
                raise RuntimeError('Update installation was cancelled; the installed app was not changed.')
            if time.monotonic() >= deadline:
                raise TimeoutError('The running application did not exit; the update was not installed.')
            time.sleep(0.25)
        backup = replace_and_restart(target, staged, Path(job['health']))
        result_path.write_text(json.dumps({'success': True, 'backup': str(backup), 'helper_pid': os.getpid()}), encoding='utf-8')
    except Exception as error:
        result_path.write_text(json.dumps({'success': False, 'error': str(error)}), encoding='utf-8')
        import traceback
        traceback.print_exc()
        if process_alive(int(job.get('parent_pid', 0))):
            return 1  # the running app reports preparation errors itself
        try:
            import tkinter as tk
            from tkinter import messagebox
            window = tk.Tk()
            window.withdraw()
            messagebox.showerror('Lab Plot Toolbox update', f'{error}\n\nDetails: {result_path.parent}', parent=window)
            window.destroy()
        except Exception:
            pass
        return 1
    return 0
