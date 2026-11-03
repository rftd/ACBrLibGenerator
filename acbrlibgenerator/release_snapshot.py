"""Persist a release's source selection before compiling, including across retries."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from .common import write_json
from .sources import SHA

ROOT = Path(__file__).resolve().parents[1]


def gh_json(*args):
    return json.loads(subprocess.check_output(['gh', *args]))


def validate(lock, config):
    if not isinstance(lock, dict):
        raise ValueError('Lock da release inválido')
    if not SHA.fullmatch(str(lock.get('commit', ''))):
        raise ValueError('Lock da release deve conter um SHA Git completo')
    revision = str(lock.get('revision', ''))
    if not revision.isdigit() or int(revision) < int(config['start_revision']):
        raise ValueError('Revisão da release inválida')
    if lock.get('branch') != config['branch'] or lock.get('svn_url') != config['svn_url'].rstrip('/'):
        raise ValueError('A origem do lock da release difere da configuração SVN')
    return {key: lock[key] for key in ('branch', 'commit', 'revision', 'svn_url')}


def release_info(repository, release_id, tag):
    info = gh_json('api', f'repos/{repository}/releases/{release_id}')
    if info['tag_name'] != tag:
        raise ValueError('A tag não corresponde à release solicitada')
    return info


def restore(repository, release_id, tag, config, output):
    release_info(repository, release_id, tag)
    pages = gh_json('api', '--paginate', '--slurp',
                    f'repos/{repository}/releases/{release_id}/assets?per_page=100')
    assets = [asset for page in pages for asset in page if asset['name'] == 'source-lock.json']
    if not assets:
        return False
    if len(assets) != 1:
        raise ValueError('Mais de um source-lock.json encontrado na release')
    lock = gh_json('api', f"repos/{repository}/releases/assets/{assets[0]['id']}",
                   '-H', 'Accept: application/octet-stream')
    write_json(output, validate(lock, config))
    return True


def freeze(repository, release_id, tag, config, state, output):
    release_info(repository, release_id, tag)
    lock = validate({'branch': state['branch'], 'commit': state['commit'],
                     'revision': state['revision'], 'svn_url': state['url']}, config)
    write_json(output, lock)
    # Never replace a previously selected snapshot. A retry restores it instead.
    subprocess.run(['gh', 'release', 'upload', tag, str(output), '--repo', repository], check=True)
    return lock


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['restore', 'freeze'])
    parser.add_argument('--repository', required=True, help='owner/repository')
    parser.add_argument('--release-id', required=True, type=int)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/mirror.json')
    parser.add_argument('--state', type=Path, default=Path('dist/mirror-state.json'))
    parser.add_argument('--output', type=Path, default=Path('dist/source-lock.json'))
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    if args.stage == 'restore':
        restored = restore(args.repository, args.release_id, args.tag, config, args.output)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                print(f'restored={str(restored).lower()}', file=stream)
        print('Reutilizando os fontes fixados na release.' if restored else 'Nova release: sincronizar SVN antes de compilar.')
    else:
        freeze(args.repository, args.release_id, args.tag, config,
               json.loads(args.state.read_text()), args.output)
    return 0
