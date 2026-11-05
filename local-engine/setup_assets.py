"""Explicit, verified model/renderer downloads. Never runs inference."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from common import HERE, HOME, digest, write_json


def verify(path, item):
    if not path.is_file() or path.stat().st_size != item['bytes']:
        return False
    if 'sha256' in item:
        return digest(path) == item['sha256']
    raw = path.read_bytes()
    return hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest() == item['git_blob_sha1']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-cache', type=Path, help='Optional existing verified asset directory; never required at runtime')
    args = parser.parse_args()
    manifest = json.loads((HERE / 'assets.json').read_text())
    HOME.mkdir(parents=True, exist_ok=True)
    records = []
    for item in manifest['assets']:
        target = HOME / item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not verify(target, item):
                raise ValueError(f'Existing asset mismatch; preserving {target}')
        else:
            cached = args.reuse_cache / item['path'] if args.reuse_cache else None
            if cached and verify(cached, item):
                shutil.copyfile(cached, target)
            else:
                partial = target.with_name(target.name + '.partial')
                if partial.exists():
                    raise ValueError(f'Preserving incomplete earlier download: {partial}')
                subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error', '--proto', '=https',
                                '--proto-redir', '=https', '--max-time', '120', '--max-filesize', str(item['bytes']),
                                item['url'], '-o', str(partial)], check=True, timeout=125)
                if not verify(partial, item):
                    raise ValueError(f'Asset verification failed; retained {partial}')
                partial.rename(target)
        records.append({'path': item['path'], 'sha256': digest(target), 'bytes': target.stat().st_size})
        print(f'Verified {item["path"]}', flush=True)
    wheel = HOME / 'downloads/basic_pitch-0.4.0-py2.py3-none-any.whl'
    destination = HOME / 'basic-pitch'
    with zipfile.ZipFile(wheel) as package:
        for item in package.infolist():
            if not (destination / item.filename).resolve().is_relative_to(destination.resolve()):
                raise ValueError('Invalid wheel path')
        if destination.exists():
            for item in package.infolist():
                if not item.is_dir() and (destination / item.filename).read_bytes() != package.read(item):
                    raise ValueError('Extracted Basic Pitch differs from its pinned wheel')
        else:
            stage = Path(tempfile.mkdtemp(prefix='.extract-', dir=HOME))
            try:
                package.extractall(stage)
                stage.rename(destination)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
    binary = HOME / 'renderer'
    subprocess.run(['cc', '-O2', '-std=c99', '-I', str(HOME), str(HERE / 'renderer.c'), '-lm', '-o', str(binary)], check=True, timeout=30)
    write_json(HOME / 'setup.json', {'schemaVersion': 1, 'assets': records,
                                   'rendererSourceSha256': digest(HERE / 'renderer.c'), 'rendererSha256': digest(binary),
                                   'model': manifest['model'], 'revision': manifest['revision']})
    print('Local engine assets prepared. No inference or audio upload occurred.')


if __name__ == '__main__':
    main()
