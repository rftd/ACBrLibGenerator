"""One-way git-svn mirror. Never merges automation or writes back to SVN."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess

from .common import write_json

META = re.compile(r'^git-svn-id: (.+)@(\d+) ([0-9a-f-]+)$', re.M)


def svn_identity(message):
    matches = META.findall(message)
    if len(matches) != 1:
        raise ValueError('O commit deve conter exatamente um git-svn-id válido')
    url, revision, uuid = matches[0]
    return {'url': url.rstrip('/'), 'revision': revision, 'uuid': uuid}


def git_env():
    env = dict(os.environ)
    token = env.get('GH_TOKEN')
    if token:
        # Authorization applies only to GitHub, never to the SVN origin.
        header = base64.b64encode(f'x-access-token:{token}'.encode()).decode()
        count = int(env.get('GIT_CONFIG_COUNT', '0'))
        env[f'GIT_CONFIG_KEY_{count}'] = 'http.https://github.com/.extraheader'
        env[f'GIT_CONFIG_VALUE_{count}'] = f'AUTHORIZATION: basic {header}'
        env['GIT_CONFIG_COUNT'] = str(count + 1)
    return env


def git(folder, *args, check=True):
    result = subprocess.run(['git', '-c', f'safe.directory={Path(folder).resolve()}', '-C', str(folder), *map(str, args)], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=git_env())
    if check and result.returncode:
        # Do not echo credential-bearing command lines or headers.
        raise RuntimeError(f'git {args[0]} falhou: {result.stderr.strip()}')
    return result


def metadata(folder, commit):
    sha = git(folder, 'rev-parse', '--verify', f'{commit}^{{commit}}').stdout.strip()
    identity = svn_identity(git(folder, 'show', '-s', '--format=%B', sha).stdout)
    return {'commit': sha, **identity}


def check_blobs(folder, commit, previous=None, limit=100 * 1024 * 1024):
    args = ['rev-list', '--objects', commit]
    if previous:
        args.append(f'^{previous}')
    objects = git(folder, *args).stdout
    result = subprocess.run(['git', '-C', str(folder), 'cat-file', '--batch-check=%(objecttype) %(objectsize) %(rest)'],
                            input=objects, text=True, capture_output=True, check=True)
    for line in result.stdout.splitlines():
        parts = line.split(' ', 2)
        if len(parts) >= 2 and parts[0] == 'blob' and int(parts[1]) >= limit:
            raise ValueError(f'Arquivo excede o limite configurado para o GitHub: {parts[-1]} ({parts[1]} bytes). O mirror não foi publicado; revise a estratégia para arquivos grandes.')


def sync(config, repository, cache, revision=None, push=False):
    branch = config['branch']
    if not branch.startswith('svn/'):
        raise ValueError('O mirror deve usar uma branch svn/*, separada de main')
    subprocess.run(['git', 'check-ref-format', f'refs/heads/{branch}'], check=True)
    start = int(config['start_revision'])
    if start < 1:
        raise ValueError('start_revision deve ser positivo')
    url = config['svn_url'].rstrip('/')
    if revision is None:
        revision = subprocess.check_output(['svn', 'info', '--show-item', 'revision', url], text=True).strip()
    if not str(revision).isdigit() or int(revision) < start:
        raise ValueError('Revisão deve ser numérica e não anterior ao início do mirror')
    cache = cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / '.git').is_dir():
        subprocess.run(['git', 'init', str(cache)], check=True, capture_output=True)
    git(cache, 'config', 'core.autocrlf', 'false')
    current_url = git(cache, 'config', '--get', 'svn-remote.svn.url', check=False).stdout.strip()
    if current_url and current_url.rstrip('/') != url:
        raise ValueError('O cache pertence a outro SVN; use uma nova pasta de cache')
    if not current_url:
        git(cache, 'svn', 'init', '--no-minimize-url', url)
    tracking = 'refs/remotes/svn/trunk'
    git(cache, 'config', 'svn-remote.svn.fetch', f':{tracking}')
    git(cache, 'config', 'svn.noMetadata', 'false')
    git(cache, 'config', '--replace-all', 'remote.origin.url', repository)
    remote = git(cache, 'ls-remote', '--heads', 'origin', f'refs/heads/{branch}').stdout.strip()
    previous = remote.split()[0] if remote else None
    local = git(cache, 'rev-parse', '--verify', tracking, check=False).stdout.strip()
    if previous:
        git(cache, 'fetch', '--no-tags', 'origin', f'refs/heads/{branch}:refs/remotes/published/trunk')
        published = metadata(cache, previous)
        if published['url'] != url:
            raise ValueError('A branch remota pertence a outro SVN')
        if int(published['revision']) > int(revision):
            raise ValueError('Sincronização não pode retroceder o mirror publicado')
        if not local:
            # git svn reconstructs its revision map from preserved git-svn-id trailers.
            git(cache, 'update-ref', tracking, previous)
    local = git(cache, 'rev-parse', '--verify', tracking, check=False).stdout.strip()
    lower = start
    if local:
        current = metadata(cache, local)
        if int(current['revision']) > int(revision):
            raise ValueError('Cache está à frente da revisão solicitada; use outro cache')
        lower = int(current['revision']) + 1
    if lower <= int(revision):
        git(cache, 'svn', 'fetch', '--log-window-size=100', '-r', f'{lower}:{revision}')
    state = metadata(cache, tracking)
    if state['url'] != url:
        raise ValueError('Origem do commit importado difere da configuração SVN')
    if previous:
        git(cache, 'merge-base', '--is-ancestor', previous, state['commit'])
    check_blobs(cache, state['commit'], previous, config.get('max_blob_bytes', 100 * 1024 * 1024))
    state.update(branch=branch, changed=state['commit'] != previous, requested_revision=str(revision))
    if push and state['changed']:
        git(cache, 'push', 'origin', f"{state['commit']}:refs/heads/{branch}")
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'config/mirror.json')
    parser.add_argument('--repository', required=True)
    parser.add_argument('--cache', type=Path, default=Path('build/svn-mirror'))
    parser.add_argument('--revision', help='vazio consulta HEAD do SVN')
    parser.add_argument('--push', action='store_true', help='publicar somente a branch do mirror')
    parser.add_argument('--output', type=Path, default=Path('dist/mirror-state.json'))
    args = parser.parse_args(argv)
    try:
        state = sync(json.loads(args.config.read_text()), args.repository, args.cache, args.revision or None, args.push)
        write_json(args.output, state)
        print(json.dumps(state, indent=2))
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
                for key in ('commit', 'revision', 'changed'):
                    print(f'{key}={str(state[key]).lower()}', file=out)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'ERRO: {error}', file=__import__('sys').stderr)
        return 1
