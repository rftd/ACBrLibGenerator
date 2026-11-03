"""Build a resolved mirror commit; the image only supplies the Pascal toolchain."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
release = os.environ.get('GITHUB_EVENT_NAME') == 'release'
libraries = 'all' if release else os.environ.get('LIBRARIES', 'all')
monitor = release or os.environ.get('MONITOR', 'true') == 'true'
installer = release or os.environ.get('INSTALLER', 'true') == 'true'
commit = os.environ['SOURCE_COMMIT']
revision = os.environ['SOURCE_REVISION']
sources = root / 'build/acbr'
args = ['build', '--libraries', libraries, '--monitor' if monitor else '--no-monitor',
        '--expected-revision', revision, '--expected-commit', commit]
if os.name == 'nt':
    args += ['--profile', 'windows', '--sources', str(sources), '--environment', 'C:/acbr-env',
             '--output', 'dist', '--installer' if monitor and installer else '--no-installer']
    if monitor and installer:
        args += ['--iscc', os.environ['ISCC_PATH']]
    command = [sys.executable, str(root / 'scripts/acbrbuild.py'), *args]
else:
    image = os.environ.get('REQUESTED_IMAGE') or f"ghcr.io/{os.environ['GITHUB_REPOSITORY'].lower()}/builder:toolchain-v1"
    subprocess.run(['docker', 'pull', image], check=True)
    digest = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{index .RepoDigests 0}}', image], text=True).strip()
    output = (root / 'dist').resolve()
    output.mkdir(exist_ok=True)
    # Mount the selected sources at the same path stored in Lazarus package links.
    mounts = ['--mount', f'type=bind,src={output},dst=/artifacts',
              '--mount', f'type=bind,src={sources},dst=/opt/acbr',
              '--mount', f'type=bind,src={root},dst=/opt/generator,readonly']
    bootstrap = ['python3', '/opt/generator/scripts/bootstrap.py', '--profile', 'linux64',
                 '--environment', '/opt/pascal', '--sources', '/opt/acbr', '--installers', '/usr/local/bin',
                 '--state', '/opt/build-state', '--skip-pascal', '--existing-sources',
                 '--expected-commit', commit, '--expected-revision', revision]
    build = ['python3', '/opt/generator/scripts/acbrbuild.py', *args, '--profile', 'linux64',
             '--sources', '/opt/acbr', '--no-installer', '--output', '/artifacts', '--image', digest]
    # Single container: package preparation and compilation must share its writable layer.
    script = "import subprocess,sys,json; commands=json.loads(sys.argv[1]); [subprocess.run(c,check=True) for c in commands]"
    command = ['docker', 'run', '--rm', *mounts, '--entrypoint', 'python3', digest,
               '-c', script, json.dumps([bootstrap, build])]
subprocess.run(command, cwd=root, check=True)
