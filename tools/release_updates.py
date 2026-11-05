"""Write the signed update manifest (updates.json) for a release.

    python tools/release_updates.py --version 0.1.1 --notes-file RELEASE_NOTES.md \
        --base-url https://github.com/Greenbean0123/lab-plot-toolbox/releases/download/v0.1.1 \
        --asset windows-x86_64=release/LabPlotToolbox-0.1.1-Windows-x64.exe \
        --asset macos-arm64=release/LabPlotToolbox-0.1.1-macOS-arm64.zip \
        --asset macos-x86_64=release/LabPlotToolbox-0.1.1-macOS-x86_64.zip

The private key comes from the LABSTUDIO_SIGNING_KEY environment variable (a GitHub Actions secret) or from
.release-keys/update-signing-key.pem. It is never committed or packaged; only its public key ships in the app.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives import serialization  # noqa: E402

from labstudio.paths import APP_VERSION  # noqa: E402
from labstudio.update import updater  # noqa: E402

PLATFORMS = ('windows-x86_64', 'macos-arm64', 'macos-x86_64')


def normalize_pem(text):
    """Rebuild a PEM private key from text that went through copy-paste (CRLF, indentation, joined lines, BOM)."""
    text = text.replace('\ufeff', '').replace('\r', '')
    match = re.search(r'-----BEGIN ([A-Z ]+)-----(.*?)-----END \1-----', text, re.S)
    label, body = (match.group(1), match.group(2)) if match else ('PRIVATE KEY', text)
    body = re.sub(r'[^A-Za-z0-9+/=]', '', body)
    if not body:
        raise SystemExit('The signing key is empty.')
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return ('-----BEGIN %s-----\n%s\n-----END %s-----\n' % (label, '\n'.join(lines), label)).encode()


def load_key():
    text = os.environ.get('LABSTUDIO_SIGNING_KEY')
    if not text:
        text = (ROOT / '.release-keys' / 'update-signing-key.pem').read_text(encoding='utf-8-sig')
    try:
        return serialization.load_pem_private_key(normalize_pem(text), password=None)
    except ValueError:
        raise SystemExit('The signing key could not be read. Paste the whole update-signing-key.pem file, '
                         'including the BEGIN and END lines, into the LABSTUDIO_SIGNING_KEY secret.') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', required=True)
    parser.add_argument('--notes', default='')
    parser.add_argument('--notes-file', type=Path)
    parser.add_argument('--base-url', required=True, help='HTTPS folder that holds the release assets')
    parser.add_argument('--asset', action='append', default=[], metavar='PLATFORM=FILE', required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'release' / 'updates.json')
    args = parser.parse_args()
    updater.version_tuple(args.version)
    if args.version != APP_VERSION:
        parser.error(f'--version must match APP_VERSION in labstudio/paths.py ({APP_VERSION}).')
    updater.require_https(args.base_url)
    notes = args.notes_file.read_text(encoding='utf-8') if args.notes_file else args.notes
    private = load_key()
    public = base64.b64encode(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    if public != updater.bundled_config()['public_key']:
        raise SystemExit('The signing key does not match the public key built into the app (labstudio/update_config.json).')
    assets = {}
    for value in args.asset:
        key, filename = value.split('=', 1)
        if key not in PLATFORMS or key in assets:
            parser.error(f'Unsupported or duplicate platform: {key}')
        file = Path(filename)
        assets[key] = {'url': args.base_url.rstrip('/') + '/' + quote(file.name), 'size': file.stat().st_size,
                       'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                       'format': 'app-zip' if key.startswith('macos-') else 'exe'}
    payload = {'schema': 1, 'version': args.version, 'notes': notes[:4000], 'assets': assets}
    envelope = {'payload': payload, 'signature': base64.b64encode(private.sign(updater.canonical(payload))).decode()}
    for key in assets:  # check with exactly the public key the app uses
        updater.verify_manifest(envelope, updater.bundled_config()['public_key'], '0.0.0', key=key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(envelope, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Signed update manifest for {", ".join(assets)}: {args.output}')


if __name__ == '__main__':
    main()
