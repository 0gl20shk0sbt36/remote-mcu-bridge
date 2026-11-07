#!/usr/bin/env python3
"""Fetch pinned source dependencies only; no toolchain or Python installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def git(path, *args):
    return subprocess.check_output(
        ['git', '-C', str(path), *args], text=True,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}).strip()


def verify_git(path, commit):
    if git(path, 'rev-parse', 'HEAD') != commit:
        raise RuntimeError(f'{path}: HEAD does not match pinned {commit}')
    if git(path, 'status', '--porcelain', '--untracked-files=no', '--ignore-submodules=all'):
        raise RuntimeError(f'{path}: tracked files or submodules are modified')


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def acquire(dep, target, cache, offline):
    if target.exists() and any(target.iterdir()):
        return _acquire(dep, target, cache, offline)
    if offline and dep['name'] == 'ESP-IDF':
        raise RuntimeError('offline IDF requires a populated IDF_PATH including recursive submodules')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bootstrap-', dir=target.parent) as work:
        staged = Path(work) / 'source'
        _acquire(dep, staged, cache, offline)
        if target.exists():
            target.rmdir()  # only an empty destination may be replaced
        staged.rename(target)


def _acquire(dep, target, cache, offline):
    """Use exact local git-archive cache, or fetch a detached pinned checkout."""
    archive = cache / dep.get('verification', {}).get('archive', '__not_an_archive__')
    expected = dep.get('verification', {}).get('archive_sha256')
    marker = target / '.bootstrap-source.json'
    if target.exists() and any(target.iterdir()):
        if (target / '.git').exists():
            verify_git(target, dep['commit'])
        elif marker.exists() and expected:
            # Recompute an inventory so a marker alone cannot bless changed files.
            recorded = json.loads(marker.read_text())
            actual = inventory(target)
            if recorded != {'commit': dep['commit'], 'archive_sha256': expected, 'files': actual}:
                raise RuntimeError(f'{target}: archive inventory mismatch')
        else:
            raise RuntimeError(f'{target}: not a verifiable pinned checkout; use an empty destination or Git checkout')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if archive.is_file() and expected:
        if sha256(archive) != expected:
            raise RuntimeError(f'{archive}: SHA-256 mismatch')
        target.mkdir(exist_ok=True)
        with tarfile.open(archive, 'r:*') as tf:
            # These cache files are git archive outputs, not GitHub prefixed tarballs.
            # Python's data filter rejects escapes/devices; reject links entirely.
            members = tf.getmembers()
            if any(m.issym() or m.islnk() for m in members):
                raise RuntimeError(f'{archive}: links are not accepted in source archives')
            tf.extractall(target, members=members, filter='data')
        marker.write_text(json.dumps({'commit': dep['commit'], 'archive_sha256': expected,
                                      'files': inventory(target)}, sort_keys=True) + '\n')
        return
    mirror = cache / (dep['name'] + '.git')
    if offline and not mirror.exists():
        raise RuntimeError(f'offline: missing {archive.name} or Git object cache {mirror.name}')
    target.mkdir(exist_ok=True)
    subprocess.run(['git', 'init', '-q', str(target)], check=True)
    # Relative IDF submodule URLs require the canonical upstream origin even
    # when the initial object fetch comes from a local cache.
    subprocess.run(['git', '-C', str(target), 'remote', 'add', 'origin', dep['url']], check=True)
    source = str(mirror.resolve()) if mirror.exists() else dep['url']
    subprocess.run(['git', '-C', str(target), 'fetch', '--depth=1', source, dep['commit']],
                   check=True, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    subprocess.run(['git', '-C', str(target), 'checkout', '-q', '--detach', 'FETCH_HEAD'], check=True)
    verify_git(target, dep['commit'])


def inventory(path):
    # Exclude separately bootstrapped child components from parent archive inventory.
    excluded = ['.bootstrap-source.json', 'Drivers/STM32F1xx_HAL_Driver/',
                'Drivers/CMSIS/Device/ST/STM32F1xx/']
    return {p.relative_to(path).as_posix(): sha256(p)
            for p in sorted(path.rglob('*')) if p.is_file()
            and not any(p.relative_to(path).as_posix() == x or
                        p.relative_to(path).as_posix().startswith(x) for x in excluded)}


def validate_manifest(manifest):
    if manifest['schema_version'] != 1:
        raise RuntimeError('unsupported manifest schema')
    for dep in manifest['dependencies']:
        for item in [dep, *dep.get('submodules', [])]:
            if not re.fullmatch(r'[0-9a-f]{40}', item['commit']):
                raise RuntimeError('invalid commit pin')
            path = Path(item['path'])
            if path.is_absolute() or '..' in path.parts:
                raise RuntimeError('manifest paths must be repository-relative')
            if not item['url'].startswith('https://github.com/'):
                raise RuntimeError('unexpected dependency URL')
    license_index = ROOT / 'third_party/licenses/index.json'
    for record in json.loads(license_index.read_text())['files']:
        file = license_index.parent / record['file']
        if sha256(file) != record['sha256']:
            raise RuntimeError(f'license digest mismatch: {file.name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=['all', 'idf', 'stm32'], default='all')
    parser.add_argument('--offline', action='store_true', help='never fetch remote sources; requires existing SDKs or local cache')
    parser.add_argument('--check', action='store_true', help='read-only verification of selected existing Git SDKs; no fetch or writes')
    parser.add_argument('--validate-manifest', action='store_true', help='validate pins and copied license digests, then exit')
    parser.add_argument('--cache', type=Path, default=Path(os.environ.get('MCU_BRIDGE_DEPS_CACHE', ROOT / '.deps/cache')),
                        help='local Git object caches NAME.git or exact STM32 git-archive files (env MCU_BRIDGE_DEPS_CACHE)')
    parser.add_argument('--deps-dir', type=Path, default=Path(os.environ.get('MCU_BRIDGE_DEPS_DIR', ROOT / '.deps')),
                        help='source destination root (env MCU_BRIDGE_DEPS_DIR); Cube defaults to vendor/STM32CubeF1')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'third_party/manifest.json').read_text())
    validate_manifest(manifest)
    if args.validate_manifest:
        print('Manifest pins and all copied license SHA-256 digests validated.')
        return
    exports = {}
    for dep in manifest['dependencies']:
        is_idf = dep['name'] == 'ESP-IDF'
        if args.only not in ['all', 'idf' if is_idf else 'stm32']:
            continue
        default = args.deps_dir / 'esp-idf' if is_idf else ROOT / dep['path']
        target = Path(os.environ.get(dep['env']) or default).expanduser().resolve()
        if args.check:
            verify_git(target, dep['commit'])
        else:
            acquire(dep, target, args.cache, args.offline)
        if is_idf:
            if not args.check and not args.offline:
                subprocess.run(['git', '-C', str(target), 'submodule', 'update', '--init', '--recursive'], check=True,
                               env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
            status = git(target, 'submodule', 'status', '--recursive')
            # check_output.strip removes the first clean prefix; preserve it explicitly.
            raw = subprocess.check_output(['git', '-C', str(target), 'submodule', 'status', '--recursive'], text=True)
            if any(line[:1] in '-+U' for line in raw.splitlines()):
                raise RuntimeError('IDF submodules are missing/mismatched; use a populated IDF_PATH offline, or rerun online')
            child = dep['submodules'][0]
            verify_git(target / child['path'], child['commit'])
            link = git(target, 'ls-tree', 'HEAD', child['path']).split()
            if link[:3] != ['160000', 'commit', child['commit']]:
                raise RuntimeError('cJSON gitlink mismatch')
        else:
            for child in dep['submodules']:
                if args.check:
                    verify_git(target / child['path'], child['commit'])
                else:
                    acquire(child, target / child['path'], args.cache, args.offline)
            if 'FW.F1.1.8.7' not in (target / 'package.xml').read_text():
                raise RuntimeError('STM32CubeF1 package.xml patch mismatch')
        exports[dep['env']] = str(target)
    print('# Source dependencies verified; no global installs performed.')
    for key, value in exports.items():
        print(f'export {key}={shlex.quote(value)}')
    if 'IDF_PATH' in exports:
        print('# Tool installation is separate: set IDF_TOOLS_PATH to a project-local directory,')
        print('# run "$IDF_PATH/install.sh" esp32c3 if needed, then source "$IDF_PATH/export.sh".')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        print(f'bootstrap_deps: {exc}', file=sys.stderr)
        sys.exit(1)
