import argparse
import json
import os
from pathlib import Path
import sys
import subprocess

from . import __version__
from .build import describe, execute, plan
from .catalog import Catalog
from .common import lazbuild, primary_config

ROOT = Path(__file__).resolve().parents[1]


def csv(value):
    return [s.strip() for s in value.split(",") if s.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compila ACBrLibs, ACBrMonitor e instaladores Windows.")
    parser.add_argument("--version", action="version", version=f"ACBrLibGenerator {__version__}")
    parser.add_argument("action", choices=["inspect", "plan", "build"])
    parser.add_argument("--sources", type=Path, default=Path(os.environ.get("ACBR_SOURCES", "/opt/acbr")))
    parser.add_argument("--environment", type=Path, default=Path(os.environ.get("PASCAL_ENVIRONMENT", "/opt/pascal")))
    parser.add_argument("--config", type=Path, default=ROOT / "config/builds.json")
    parser.add_argument("--profile", default="linux64")
    parser.add_argument("--libraries", help="all ou nomes separados por vírgula; vazio desativa bibliotecas")
    parser.add_argument("--lib-modes", type=csv)
    parser.add_argument("--monitor", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--monitor-modes", type=csv)
    parser.add_argument("--installer", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--lazbuild", type=Path)
    parser.add_argument("--pcp", type=Path)
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--expected-revision")
    parser.add_argument("--expected-commit")
    parser.add_argument("--image", default=os.environ.get("BUILDER_IMAGE", ""), help="imagem/digest para registrar no manifesto")
    args = parser.parse_args(argv)
    try:
        catalog = Catalog.load(args.sources)
        if args.action == "inspect":
            projects = [*catalog.libraries, *([catalog.monitor] if catalog.monitor else [])]
            print(json.dumps([p.describe() for p in projects], ensure_ascii=False, indent=2))
            return 0
        profiles = json.loads(args.config.read_text(encoding="utf-8"))
        if args.profile not in profiles:
            raise ValueError(f"Perfil desconhecido: {args.profile}; disponíveis: {', '.join(profiles)}")
        profile = dict(profiles[args.profile])
        if args.libraries is not None:
            profile["libraries"] = "all" if args.libraries == "all" else csv(args.libraries)
        for key in ("library_modes", "monitor_modes", "monitor", "installer"):
            value = getattr(args, "lib_modes" if key == "library_modes" else key)
            if value is not None:
                profile[key] = value
        tool = [str(args.lazbuild or lazbuild(args.environment))]
        pcp = args.pcp or primary_config(args.environment)
        if args.action == "plan":
            jobs, skipped = plan(catalog, profile)
            print(json.dumps({"jobs": describe(jobs, args.output.resolve() / "preview", tool, pcp, not args.incremental),
                              "skipped": skipped}, ensure_ascii=False, indent=2))
        else:
            directory = execute(catalog, profile, args.environment, args.output, tool=tool, pcp=pcp,
                                iscc=args.iscc, rebuild=not args.incremental, expected_revision=args.expected_revision, expected_commit=args.expected_commit,
                                metadata={"profile": args.profile, "image": args.image})
            print(f"Resultado: {directory}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
