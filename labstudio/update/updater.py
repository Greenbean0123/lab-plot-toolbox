"""Signed update discovery and downloads (adapted from XRD Plotter). No network requests on import.

The release manifest (updates.json) is signed with an Ed25519 key kept by the publisher; the app only holds the
public key (labstudio/update_config.json). Packages are verified by size and SHA-256 before anything is installed.
"""
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from ..paths import APP_VERSION, data_directory as app_data_directory

MAX_DOWNLOAD = 1024 * 1024 * 1024
APP_BUNDLE = 'Lab Plot Studio.app'
BUNDLE_ID = 'io.github.greenbean0123.labplotstudio'
HEALTH_ENV = 'LABSTUDIO_UPDATE_HEALTH_FILE'


def data_directory():
    return app_data_directory()


def bundled_config():
    return json.loads((Path(__file__).resolve().parent.parent / 'update_config.json').read_text(encoding='utf-8'))


def preferences():
    result = dict(bundled_config(), automatic=True)
    try:
        saved = json.loads((data_directory() / 'updates.json').read_text(encoding='utf-8'))
        result['automatic'] = bool(saved['automatic'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result


def save_preferences(automatic):
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'updates.json').write_text(json.dumps({'automatic': bool(automatic)}, indent=2), encoding='utf-8')


def require_https(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Update URLs must use HTTPS without embedded credentials.')


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Release versions must use major.minor.patch, such as 0.1.0.')
    return tuple(map(int, version.split('.')))


def platform_key():
    machine = platform.machine().lower()
    arch = {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)
    system = {'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, sys.platform)
    return f'{system}-{arch}'


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def verify_manifest(envelope, public_key, current_version, key=None):
    """Return the release if it is newer and valid for this platform, None if not newer; raise if invalid."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        payload = envelope['payload']
        signature = base64.b64decode(envelope['signature'], validate=True)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(signature, canonical(payload))
    except (KeyError, ValueError, TypeError, InvalidSignature) as error:
        raise ValueError('Update manifest signature verification failed.') from error
    if payload.get('schema') != 1:
        raise ValueError('Unsupported update manifest schema.')
    if version_tuple(payload['version']) <= version_tuple(current_version):
        return None
    key = key or platform_key()
    asset = payload.get('assets', {}).get(key)
    if not isinstance(asset, dict):
        raise ValueError(f'This release does not include a package for {key}.')
    require_https(asset['url'])
    if not re.fullmatch(r'[0-9a-fA-F]{64}', str(asset.get('sha256', ''))):
        raise ValueError('Invalid update checksum.')
    if type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_DOWNLOAD:
        raise ValueError('Invalid update size.')
    if asset.get('format') != ('app-zip' if key.startswith('macos-') else 'exe'):
        raise ValueError('The update package format does not match this platform.')
    return dict(version=payload['version'], notes=str(payload.get('notes', '')), platform=key, asset=asset, envelope=envelope)


def open_https(url):
    require_https(url)

    class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            require_https(newurl)
            return super().redirect_request(request, fp, code, message, headers, newurl)
    opener = urllib.request.build_opener(HTTPSRedirects())
    response = opener.open(urllib.request.Request(url, headers={
        'User-Agent': f'LabPlotStudio-Updater/{APP_VERSION}', 'Accept': 'application/json, application/octet-stream'}), timeout=20)
    require_https(response.geturl())
    return response


def check(current_version=APP_VERSION, config=None):
    config = config or preferences()
    if not config.get('manifest_url') or not config.get('public_key'):
        raise ValueError('This build has no update server or signing key configured.')
    try:
        with open_https(config['manifest_url']) as response:
            raw = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:  # no release with a manifest has been published yet
            return None
        raise
    if len(raw) > 1024 * 1024:
        raise ValueError('Update manifest is too large.')
    return verify_manifest(json.loads(raw), config['public_key'], current_version)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download(release, progress=None):
    asset = release['asset']
    directory = data_directory() / 'updates' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    package = directory / ('package.zip' if asset['format'] == 'app-zip' else 'package.exe')
    partial = package.with_suffix('.partial')
    digest, received = hashlib.sha256(), 0
    try:
        with open_https(asset['url']) as response, partial.open('wb') as stream:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > asset['size']:
                    raise ValueError('Downloaded update exceeds the signed size.')
                digest.update(chunk)
                stream.write(chunk)
                if progress:
                    progress(received, asset['size'])
        if received != asset['size'] or digest.hexdigest() != asset['sha256'].lower():
            raise ValueError('Update checksum or size verification failed. The installed program was not changed.')
        partial.replace(package)
        return package
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def is_onefile():
    """PyInstaller single-file builds unpack to a temporary folder separate from the executable."""
    meipass = getattr(sys, '_MEIPASS', None)
    return bool(meipass) and Path(meipass).resolve() != Path(sys.executable).resolve().parent \
        and Path(meipass).resolve() != Path(sys.executable).resolve().parent / '_internal'


def installed_target():
    """The file or bundle that an update replaces; raises with a user-facing reason if it cannot be replaced."""
    if not getattr(sys, 'frozen', False):
        raise ValueError('Automatic installation works in the packaged app. Running from source: use git pull instead.')
    executable = Path(sys.executable).resolve()
    if sys.platform == 'darwin':
        target = executable.parents[2]
        if target.suffix != '.app':
            raise ValueError('The running program is not a standalone Mac app.')
        if str(target).startswith('/Volumes/'):
            raise ValueError('Move Lab Plot Studio to Applications before updating; do not run it from the disk image.')
    elif sys.platform == 'win32':
        if not is_onefile():
            raise ValueError('The folder version cannot replace itself. Download the new version from the release page.')
        target = executable
    else:
        raise ValueError('Automatic installation supports Windows and macOS only.')
    if not os.access(target.parent, os.W_OK):
        raise ValueError('The application folder is not writable. Move the app to a folder you own before updating.')
    return target


def start_installer(package, release):
    """Copy the running app as a helper and start it with --apply-update; returns (process, ready_file)."""
    target = installed_target()
    folder = package.parent
    helper_folder = folder / 'helper'
    helper_folder.mkdir()
    if sys.platform == 'darwin':
        helper_app = helper_folder / 'Lab Plot Studio Updater.app'
        shutil.copytree(target, helper_app, symlinks=True)
        executable = helper_app / 'Contents' / 'MacOS' / Path(sys.executable).name
    else:
        executable = helper_folder / 'LabPlotStudio-Updater.exe'
        shutil.copy2(sys.executable, executable)
    job = {'target': str(target), 'package': str(package), 'release': release,
           'parent_pid': os.getpid(), 'parent_bootloader_pid': os.getppid(),
           'ready': str(folder / 'helper-ready'), 'health': str(folder / 'new-app-ready'),
           'result': str(folder / 'result.json')}
    job_path = folder / 'job.json'
    job_path.write_text(json.dumps(job), encoding='utf-8')
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {'start_new_session': True}
    with (folder / 'installer.log').open('ab') as log:
        process = subprocess.Popen([str(executable), '--apply-update', str(job_path)],
                                   cwd=folder, env=env, stdout=log, stderr=log, **kwargs)
    return process, Path(job['ready'])


def mark_ready():
    """Called by a freshly updated app once its window is up: tells the installer the update worked."""
    value = os.environ.pop(HEALTH_ENV, None)
    if not value:
        return
    path = Path(value).resolve()
    base = (data_directory() / 'updates').resolve()
    if path.name == 'new-app-ready' and path.parent.parent == base:
        path.write_text('ready')
