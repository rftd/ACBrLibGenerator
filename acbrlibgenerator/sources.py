"""Resolve immutable mirrored sources for manual builds and release tags."""
import argparse
import json
from pathlib import Path
import re
import tempfile
from .mirror import git, metadata
from .common import write_json

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r'^[0-9a-f]{40}$')


def resolve(repository, ref, config):
    if not SHA.fullmatch(ref or '') and ref != config['branch']:
        raise ValueError('Use um SHA Git completo ou a branch configurada do mirror')
    with tempfile.TemporaryDirectory(prefix='acbr-source-') as temporary:
        folder = Path(temporary)
        git(folder, 'init')
        git(folder, 'remote', 'add', 'origin', repository)
        git(folder, 'fetch', '--depth=1', '--no-tags', 'origin', ref)
        state = metadata(folder, 'FETCH_HEAD')
    if state['url'] != config['svn_url'].rstrip('/'):
        raise ValueError('O commit selecionado não pertence ao SVN configurado')
    return {'branch': config['branch'], 'commit': state['commit'], 'revision': state['revision'], 'svn_url': state['url']}


def verify(folder, lock):
    state = metadata(folder, 'HEAD')
    if not SHA.fullmatch(lock.get('commit') or ''):
        raise ValueError('Fixe primeiro um commit do mirror em config/sources.lock.json')
    if (state['commit'], state['revision'], state['url']) != (lock['commit'], str(lock['revision']), lock['svn_url'].rstrip('/')):
        raise ValueError('Os fontes não correspondem ao SHA/revisão/origem fixados')
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--ref', help='vazio usa o commit fixado; necessário na primeira sincronização')
    parser.add_argument('--lock', type=Path, default=ROOT / 'config/sources.lock.json')
    parser.add_argument('--config', type=Path, default=ROOT / 'config/mirror.json')
    parser.add_argument('--output', type=Path, default=Path('dist/source-lock.json'))
    parser.add_argument('--pin', action='store_true', help='atualiza o lock local para revisão e commit selecionados')
    args = parser.parse_args(argv)
    cfg = json.loads(args.config.read_text())
    existing = json.loads(args.lock.read_text())
    ref = args.ref or existing.get('commit')
    if not ref:
        raise ValueError('O lock ainda não foi fixado. Sincronize o mirror e use --ref svn/trunk --pin.')
    lock = resolve(args.repository, ref, cfg)
    if not args.ref and lock != existing:
        raise ValueError('O lock de fontes difere dos metadados do commit remoto')
    write_json(args.output, lock)
    if args.pin:
        write_json(args.lock, lock)
    import os
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            print(f"commit={lock['commit']}", file=output)
            print(f"revision={lock['revision']}", file=output)
    print(json.dumps(lock, indent=2))
    return 0
