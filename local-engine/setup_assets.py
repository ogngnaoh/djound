"""Explicit, verified model/renderer downloads. Never runs inference."""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from common import HERE, HOME, digest, write_json


def is_planner_asset(manifest, item):
    return Path(item['path']).parts[0] == manifest['modelDirectory']


def verify(path, item):
    if not path.is_file() or path.stat().st_size != item['bytes']:
        return False
    if 'sha256' in item:
        return digest(path) == item['sha256']
    raw = path.read_bytes()
    return hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest() == item['git_blob_sha1']


def install_recording_model(directory, cache):
    manifest = json.loads((HERE / f'{directory}-assets.json').read_text())
    target = HOME / directory
    identity = {key: manifest[key] for key in ('model', 'revision')}
    HOME.mkdir(parents=True, exist_ok=True)
    repair = directory == 'instrument-transcriber'
    existing = target.exists()
    with tempfile.TemporaryDirectory(prefix=f'.{directory}-', dir=HOME) as tmp:
        staged = Path(tmp) / 'ready'
        staged.mkdir()
        for item in manifest['assets']:
            name = item['path']
            if existing:
                if verify(target / name, item):
                    continue
                if not repair:
                    raise ValueError(f'Existing {directory} asset differs: {name}')
            output = staged / name
            if cache:
                if not verify(cache / name, item):
                    raise ValueError(f'Cached {directory} asset differs: {name}')
                try:
                    os.link((cache / name).resolve(strict=True), output)
                except OSError as error:
                    if error.errno != errno.EXDEV:
                        raise
                    shutil.copyfile(cache / name, output)
            else:
                url = item.get('url') or f'https://huggingface.co/{manifest["model"]}/resolve/{manifest["revision"]}/{name}'
                subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error', '--proto', '=https',
                                '--proto-redir', '=https', '--max-time', '1800', '--max-filesize', str(item['bytes']),
                                url, '-o', output], check=True)
            if not verify(output, item):
                raise ValueError(f'Model asset failed verification: {name}')
        receipt = '.djound-verified.json'
        if existing:
            try:
                correct_receipt = json.loads((target / receipt).read_text()) == identity
            except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
                correct_receipt = False
            if not correct_receipt and not repair:
                raise ValueError(f'The existing {directory} receipt differs from its pinned release.')
            if not correct_receipt:
                write_json(staged / receipt, identity)
            # Verify every replacement before changing the installed directory.
            # Rename files atomically on the same volume, retaining healthy and
            # unrelated files. The verification receipt goes in last.
            pending = sorted(staged.iterdir(), key=lambda file: file.name == receipt)
            if pending and target.is_symlink():
                raise ValueError(f'Repair the {directory} installation at its real path: {target.resolve()}')
            for file in pending:
                file.replace(target / file.name)
        else:
            write_json(staged / receipt, identity)
            try:
                staged.rename(target)
            except OSError as error:
                # Another setup can finish while this one is downloading. Accept
                # only the complete, independently verified pinned installation.
                if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    raise
                if (not all(verify(target / item['path'], item) for item in manifest['assets'])
                        or json.loads((target / receipt).read_text()) != identity):
                    raise
    print(f'Verified {directory}:', manifest['model'])


def prepare_renderer(name='renderer', key='renderer'):
    binary = HOME / name
    source = HERE / (name + '.c')
    source_hash = digest(source)
    receipt = HOME / 'setup.json'
    if receipt.is_file() and binary.is_file():
        try:
            saved = json.loads(receipt.read_text())
        except (OSError, ValueError):
            saved = {}
        if saved.get(key + 'SourceSha256') == source_hash and saved.get(key + 'Sha256') == digest(binary):
            return source_hash, saved[key + 'Sha256']
    with tempfile.TemporaryDirectory(prefix='.renderer-', dir=HOME) as folder:
        staged = Path(folder) / 'renderer'
        subprocess.run(['cc', '-O2', '-std=c99', '-I', str(HOME), str(source), '-lm', '-o', str(staged)],
                       check=True, timeout=30)
        build_hash = digest(staged)
        staged.replace(binary)
    return source_hash, build_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-cache', type=Path, help='Optional existing verified asset directory; never required at runtime')
    parser.add_argument('--audio-only', action='store_true', help='Skip the optional freeform planner weights')
    parser.add_argument('--finished-mix', action='store_true', help='Install just the finished mix separator')
    parser.add_argument('--planner-only', action='store_true', help='Install only the language planner weights')
    recording = parser.add_mutually_exclusive_group()
    recording.add_argument('--score-director', dest='recording_model', action='store_const', const='score-director',
                           help='Install the recording arrangement model')
    recording.add_argument('--instrument-transcriber', dest='recording_model', action='store_const',
                           const='instrument-transcriber', help='Install the instrument transcription model')
    args = parser.parse_args()
    if args.recording_model:
        install_recording_model(args.recording_model, args.reuse_cache)
        return
    manifest = json.loads((HERE / 'assets.json').read_text())
    mix_assets = json.loads((HERE / 'finished-mix-assets.json').read_text())['assets']
    if args.planner_only:
        assets = [item for item in manifest['assets'] if is_planner_asset(manifest, item)]
    else:
        assets = mix_assets if args.finished_mix else manifest['assets'] + ([] if args.audio_only else mix_assets)
    HOME.mkdir(parents=True, exist_ok=True)
    records = []
    for item in assets:
        if args.audio_only and is_planner_asset(manifest, item):
            continue
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
    if args.finished_mix or args.planner_only:
        return
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
    source_hash, binary_hash = prepare_renderer()
    score_source, score_binary = prepare_renderer('score_renderer', 'scoreRenderer')
    write_json(HOME / 'setup.json', {'schemaVersion': 1, 'assets': records,
                                   'rendererSourceSha256': source_hash, 'rendererSha256': binary_hash,
                                   'scoreRendererSourceSha256': score_source, 'scoreRendererSha256': score_binary,
                                   'model': manifest['model'], 'revision': manifest['revision']})
    print('Local engine assets prepared. No inference or audio upload occurred.')


if __name__ == '__main__':
    main()
