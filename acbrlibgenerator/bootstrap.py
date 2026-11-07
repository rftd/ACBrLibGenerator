"""Provision the image/Windows runner using PascalInstall and ACBrInstall CLIs."""

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

from .build import plan, source_info
from .catalog import Catalog, xml
from .common import Runner, lazbuild, primary_config, sha256, write_json
from .source_patches import apply_compatibility

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"win32": ("i386", "win32"), "win64": ("x86_64", "win64"),
           "linux32": ("i386", "linux"), "linux64": ("x86_64", "linux")}


def install_pascal(environment, pascal, cfg, cross, build_options, runner):
    for name in cross:
        if name not in TARGETS:
            raise ValueError(f'Alvo cross desconhecido: {name}')
    # The pinned installer passes source URLs to cross builds. fpclazup rejects
    # these options in cross mode, so prepare native tools through PascalInstall
    # and build cross targets from those already selected sources separately.
    runner.run([pascal, '--cli', '--dir', environment, '--fpc', cfg['fpc'], '--laz', cfg['lazarus'],
                '--cross', '', *build_options])
    if not cross:
        return
    bootstrap = environment / '_fpcup' / ('fpclazup.exe' if os.name == 'nt' else 'fpclazup')
    if not bootstrap.is_file():
        raise ValueError(f'Bootstrap do PascalInstall não encontrado: {bootstrap}')
    for name in dict.fromkeys(cross):
        cpu, target_os = TARGETS[name]
        runner.say(f'Instalando cross-compiler {name} com os fontes FPC/Lazarus já preparados.')
        # CleanModule initializes cross tools/libs before BuildModule checks them.
        runner.run([bootstrap, f'--installdir={environment}', f'--cputarget={cpu}', f'--ostarget={target_os}',
                    '--only=FPCCleanOnly,FPCBuildOnly,LazarusBuildOnly', '--noconfirm', '--verbose'])


def read_toolchain(path):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not cfg["acbr_revision"].isdigit() or int(cfg["acbr_revision"]) <= 0:
        raise ValueError("acbr_revision deve ser uma revisão SVN numérica fixa")
    ref = cfg["installers_ref"]
    if len(ref) != 40 or any(c not in "0123456789abcdef" for c in ref):
        raise ValueError("installers_ref deve ser um commit Git completo")
    if not cfg["acbr_svn_url"].startswith("https://svn.code.sf.net/p/acbr/code/"):
        raise ValueError("Use o SVN oficial do ACBr")
    return cfg


def prepare_sources(cfg, destination: Path, runner):
    revision = cfg["acbr_revision"]
    if destination.exists() and any(destination.iterdir()):
        if not (destination / ".svn").is_dir():
            raise ValueError(f"A pasta de fontes já contém arquivos sem controle SVN: {destination}")
        actual = subprocess.check_output(["svn", "info", "--show-item", "revision", str(destination)], text=True).strip()
        url = subprocess.check_output(["svn", "info", "--show-item", "url", str(destination)], text=True).strip()
        if actual != revision or url.rstrip("/") != cfg["acbr_svn_url"].rstrip("/"):
            raise ValueError(f"Fontes existentes não correspondem à revisão/URL configurada: {actual}, {url}")
    else:
        runner.run(["svn", "checkout", "--non-interactive", "--depth", "empty", "-r", revision, cfg["acbr_svn_url"], destination])
    folders = ["Fontes", "Pacotes/Lazarus", "Projetos/ACBrLib/Fontes", "Projetos/ACBrMonitorPLUS", "Exemplos/ACBrDFe/Schemas"]
    if os.name == "nt":
        folders.append("DLLs")
    for folder in folders:
        runner.run(["svn", "update", "--non-interactive", "--parents", "--set-depth", "infinity", "-r", revision, destination / folder])


def package_catalog(directory: Path):
    packages = {}
    for path in sorted(directory.rglob("*.lpk")):
        if any(part.casefold() in {"backup", ".git", ".svn"} for part in path.parts):
            continue
        root = xml(path)
        name = root.find("Package/Name")
        if name is not None:
            deps = [n.get("Value") for n in root.findall("Package/RequiredPkgs/*/PackageName") if n.get("Value")]
            packages.setdefault(name.get("Value").casefold(), (name.get("Value"), path, deps))
    return packages


def dependencies(sources: Path, environment: Path, projects):
    acbr = package_catalog(sources / "Pacotes/Lazarus")
    lazarus = package_catalog(environment / "lazarus/components")
    required = {name.casefold() for project in projects for name in project.packages}
    # Monitor can depend directly on an OPM package rather than an ACBr wrapper.
    # Selecting a matching wrapper lets ACBrInstall prepare that dependency.
    for external in ("frce", "lazreportpdfexport", "pack_powerpdf"):
        if external in required:
            match = next((key for key, (_, _, deps) in acbr.items() if external in [d.casefold() for d in deps]), None)
            if match:
                required.add(match)
    selected, builtin, seen = set(), set(), set()

    def visit(name):
        key = name.casefold()
        if key in seen:
            return
        seen.add(key)
        if key in acbr:
            original, _, deps = acbr[key]
            selected.add(original)
        elif key in lazarus:
            _, path, deps = lazarus[key]
            builtin.add(path)
        else:
            return  # FCL/LCL or an OPM dependency prepared by ACBrInstall.
        for dependency in deps:
            visit(dependency)

    for name in sorted(required):
        visit(name)
    if not selected:
        raise ValueError("Nenhum pacote ACBr foi resolvido para os projetos selecionados")
    return sorted(selected), sorted(builtin)


def smoke_compilers(environment: Path, cross, runner):
    root = xml(primary_config(environment) / "environmentoptions.xml")
    node = root.find(".//CompilerFilename")
    if node is None or not node.get("Value"):
        raise ValueError("CompilerFilename ausente na configuração Lazarus")
    compiler = Path(node.get("Value"))
    if not compiler.is_absolute():
        compiler = primary_config(environment) / compiler
    native = ("x86_64", "win64" if os.name == "nt" else "linux")
    wanted = [native, *(TARGETS[name] for name in cross)]
    with tempfile.TemporaryDirectory(prefix="acbr-compiler-smoke-") as temporary:
        source = Path(temporary) / "smoke.pas"
        source.write_text("program smoke; begin end.\n", encoding="utf-8")
        for cpu, os_name in dict.fromkeys(wanted):
            binary = Path(temporary) / f"smoke-{cpu}-{os_name}{'.exe' if os_name.startswith('win') else ''}"
            runner.run([compiler, f"-P{cpu}", f"-T{os_name}", f"-o{binary}", source])
            if not binary.is_file() or not binary.stat().st_size:
                raise RuntimeError(f"Toolchain {cpu}-{os_name} não gerou o programa de teste")
    return str(compiler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/toolchain.json")
    parser.add_argument("--profile", choices=["linux64", "windows"], required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--installers", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--existing-sources", action="store_true")
    parser.add_argument("--skip-pascal", action="store_true")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-revision")
    args = parser.parse_args(argv)
    args.environment = args.environment.resolve()
    args.sources = args.sources.resolve()
    args.installers = args.installers.resolve()
    runner = Runner(args.state / "bootstrap.log")
    try:
        cfg = read_toolchain(args.config)
        if (args.profile == "windows") != (os.name == "nt"):
            raise ValueError("O perfil deve corresponder ao sistema da máquina de preparação")
        if args.existing_sources:
            if not args.expected_commit or not args.expected_revision:
                raise ValueError('Fontes Git exigem --expected-commit e --expected-revision')
            state = source_info(args.sources)
            if (state.get('commit'), state.get('revision'), state.get('url')) != (args.expected_commit, args.expected_revision, cfg['acbr_svn_url'].rstrip('/')):
                raise ValueError('Fontes Git não correspondem ao SHA/revisão/origem selecionados')
            source_patches = apply_compatibility(args.sources, runner)
        else:
            prepare_sources(cfg, args.sources, runner)
            source_patches = []
        suffix = ".exe" if os.name == "nt" else ""
        pascal = args.installers / f"pascalinstall{suffix}"
        acbr = args.installers / f"acbrinstall{suffix}"
        cross = cfg["windows_cross" if os.name == "nt" else "linux_cross"]

        def build_only(tool):
            help_text = subprocess.check_output([tool, "--help"], text=True, encoding="utf-8", errors="replace")
            if "--build-only" in help_text:
                return ["--build-only"]
            runner.say(f"Compatibilidade: {tool.name} também configurará a IDE; publique a versão com --build-only para reduzir essa etapa.")
            return []

        if not args.skip_pascal:
            install_pascal(args.environment, pascal, cfg, cross, build_only(pascal), runner)
        else:
            saved = json.loads((args.state / 'environment.json').read_text())['toolchain']
            for key in ('fpc', 'lazarus', 'linux_cross' if os.name != 'nt' else 'windows_cross'):
                if saved[key] != cfg[key]:
                    raise ValueError(f'Imagem incompatível com {key}; reconstrua a imagem')
        compiler = smoke_compilers(args.environment, cross, runner)
        catalog = Catalog.load(args.sources)
        profile = json.loads((ROOT / "config/builds.json").read_text(encoding="utf-8"))[args.profile]
        jobs, _ = plan(catalog, profile)
        projects = list({job.project.path: job.project for job in jobs}.values())
        packages, builtin = dependencies(args.sources, args.environment, projects)
        tool = lazbuild(args.environment)
        pcp = primary_config(args.environment)
        for path in builtin:
            runner.run([tool, f"--pcp={pcp}", "--add-package-link", path])
        runner.run([acbr, "--cli", "--ide", "lazarus", "--dir", args.environment, "--acbr-dir", args.sources,
                    "--acbr-action", "install", "--acbr-packages", ",".join(packages), *build_only(acbr)])
        metadata = {"toolchain": cfg, "source": source_info(args.sources), "source_patches": source_patches,
                    "host": platform.platform(), "compiler": compiler, "packages": packages,
                    "installers": {p.name: {"sha256": sha256(p), "version": subprocess.check_output([p, "--version"], text=True).strip()}
                                   for p in (pascal, acbr)}}
        write_json(args.state / "environment.json", metadata)
        runner.say("Ambiente preparado e compiladores verificados.")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        runner.say(f"ERRO: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
