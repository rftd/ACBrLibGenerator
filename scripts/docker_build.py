#!/usr/bin/env python3
"""Build a local image from the same pinned configuration used in Actions."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--tag", default="acbrlibgenerator:local")
parser.add_argument("--target", choices=["base", "builder"], default="builder")
args = parser.parse_args()
cfg = json.loads((ROOT / "config/toolchain.json").read_text())
subprocess.run(["docker", "build", "--file", str(ROOT / "docker/Dockerfile"), "--target", args.target,
                "--build-arg", f"INSTALLERS_REPOSITORY={cfg['installers_repository']}",
                "--build-arg", f"INSTALLERS_REF={cfg['installers_ref']}", "--tag", args.tag, str(ROOT)], check=True)
