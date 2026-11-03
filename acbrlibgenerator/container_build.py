"""Run container builds and return artifact ownership to the host runner."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


def restore_ownership(output, uid, gid):
    # Do not follow symlinks outside the artifacts mount.
    for directory, folders, files in os.walk(output, followlinks=False):
        for name in folders + files:
            os.chown(Path(directory) / name, uid, gid, follow_symlinks=False)
        os.chown(directory, uid, gid, follow_symlinks=False)


def execute(commands, output, uid, gid, state=None):
    try:
        for command in commands:
            subprocess.run(command, check=True)
    finally:
        try:
            if state:
                for name in ('bootstrap.log', 'environment.json'):
                    source = state / name
                    if source.is_file():
                        destination = output / 'bootstrap' / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
        finally:
            # Atomic JSON files have mode 0600. chown makes even failure
            # manifests readable/uploadable without relaxing their permissions.
            restore_ownership(output, uid, gid)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uid', type=int, required=True)
    parser.add_argument('--gid', type=int, required=True)
    parser.add_argument('--output', type=Path, default=Path('/artifacts'))
    parser.add_argument('--state', type=Path, default=Path('/opt/build-state'))
    parser.add_argument('commands', help='JSON list of command argument lists')
    args = parser.parse_args(argv)
    execute(json.loads(args.commands), args.output, args.uid, args.gid, args.state)
    return 0
