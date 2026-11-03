"""Build plans, isolated outputs, archives, checksums and failure manifests."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time
import zipfile

from . import __version__, installer
from .catalog import Catalog, Mode, Project, relative_binary_path
from .common import Runner, lazbuild, primary_config, sha256, write_json
from .mirror import metadata, git


@dataclass(frozen=True)
class Job:
    project: Project
    mode: Mode
    installer: bool


def plan(catalog: Catalog, profile: dict):
    jobs, skipped = [], []
    names = profile["libraries"]
    all_libraries = names == "all"
    if not all_libraries and not isinstance(names, list):
        raise ValueError("libraries deve ser 'all' ou uma lista")
    lookup = {p.name.casefold(): p for p in catalog.libraries}
    selected = list(catalog.libraries) if all_libraries else []
    if not all_libraries:
        for name in dict.fromkeys(n.casefold() for n in names):
            if name not in lookup:
                raise ValueError(f"Biblioteca desconhecida: {name}")
            selected.append(lookup[name])
    requested = profile["library_modes"]
    known = {m.name.casefold() for p in catalog.libraries for m in p.modes}
    if selected:
        unknown = [m for m in requested if m.casefold() not in known]
        if unknown:
            raise ValueError(f"Modos de biblioteca desconhecidos: {unknown}")
    for project in selected:
        modes = {m.name.casefold(): m for m in project.modes}
        compatible = [modes[n.casefold()] for n in dict.fromkeys(requested) if n.casefold() in modes]
        if not compatible:
            if not all_libraries:
                raise ValueError(f"{project.name} não oferece os modos selecionados: {requested}")
            skipped.append(f"{project.name}: sem modo compatível")
        jobs.extend(Job(project, mode, False) for mode in compatible)
    if profile["monitor"]:
        if catalog.monitor is None:
            raise ValueError("ACBrMonitor.lpi não encontrado")
        modes = {m.name.casefold(): m for m in catalog.monitor.modes}
        for name in dict.fromkeys(profile["monitor_modes"]):
            mode = modes.get(name.casefold())
            if mode is None:
                raise ValueError(f"Modo do Monitor desconhecido: {name}")
            jobs.append(Job(catalog.monitor, mode, profile["installer"] and mode.os.startswith("win")))
    if not jobs:
        raise ValueError("Nenhum projeto/modo selecionado para compilar")
    if profile["installer"] and not any(j.installer for j in jobs):
        raise ValueError("O instalador exige o Monitor em pelo menos um modo Windows")
    return jobs, skipped


def component(value):
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", value)


def command(tool, pcp: Path, job: Job, directory: Path, rebuild: bool):
    binary = directory / job.mode.filename
    units = directory / "units"
    for path in (binary, units):
        if any(c in str(path) for c in '$"\r\n') or any(c.isspace() for c in str(path)):
            raise ValueError(f"Caminho não suportado pelas opções FPC: {path}")
    args = [*tool, f"--pcp={pcp}", "--no-write-project", f"--bm={job.mode.name}"]
    if rebuild:
        args.append("-B")
    # --opt follows the LPI options. A final -o overrides its original output path.
    args.extend([f'--opt=-o{binary} -FU{units}', str(job.project.path)])
    return args, binary


def describe(jobs, output: Path, tool, pcp: Path, rebuild=True):
    entries = []
    for index, job in enumerate(jobs, 1):
        directory = output / f"{index:03}-{component(job.project.name)}-{component(job.mode.name)}"
        args, binary = command(tool, pcp, job, directory, rebuild)
        entries.append({"project": job.project.name, "mode": job.mode.name, "target": f"{job.mode.cpu}-{job.mode.os}",
                        "command": args, "binary": str(binary), "directory": str(directory), "installer": job.installer})
    return entries


def source_info(root: Path):
    result = {"path": str(root), "revision": None}
    if (root / ".git").exists():
        state = metadata(root, "HEAD")
        result.update(state)
        result["modified_files"] = git(root, "diff", "HEAD", "--name-only").stdout.splitlines()
        return result
    if (root / ".svn").is_dir() and shutil.which("svn"):
        output = subprocess.check_output(["svn", "info", "--show-item", "revision", str(root)], text=True)
        result["revision"] = output.strip()
    return result


def archive(job: Job, binary: Path, directory: Path, sources: Path):
    version = component(job.project.version or "unversioned")
    destination = directory / f"{component(job.project.name)}-{version}-{component(job.mode.name)}.zip"
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as output:
        target = relative_binary_path(job.project, job.mode) if job.project.library else Path(binary.name)
        output.write(binary, target.as_posix())
        for log in [job.project.path.with_name(f"{job.project.name}-change-log.txt"),
                    sources / "Projetos/ACBrLib/Fontes/Comum/ACBrLibComum-change-log.txt"]:
            if log.is_file() and (job.project.library or log.parent == job.project.path.parent):
                output.write(log, f"ChangeLog/{log.name}")
        if job.project.library:
            schemas = sources / "Exemplos/ACBrDFe/Schemas"
            name = job.project.path.parent.name.casefold()
            if schemas.is_dir():
                for folder in schemas.iterdir():
                    if folder.is_dir() and folder.name.casefold() == name:
                        for path in sorted(folder.rglob("*")):
                            if path.is_file() and not path.is_symlink():
                                output.write(path, f"Schemas/{folder.name}/{path.relative_to(folder).as_posix()}")
    return destination


def execute(catalog, profile, environment: Path, output: Path, *, tool=None, pcp=None, iscc=None,
            rebuild=True, runner_factory=Runner, expected_revision=None, metadata=None, expected_commit=None):
    jobs, skipped = plan(catalog, profile)
    tool = tuple(tool or [str(lazbuild(environment))])
    pcp = pcp or primary_config(environment)
    if any(j.installer for j in jobs):
        if os.name != "nt":
            raise ValueError("O instalador do Monitor deve ser gerado no Windows com Inno Setup")
        iscc = Path(iscc or shutil.which("ISCC.exe") or "")
        if not iscc.is_file():
            raise ValueError("Informe --iscc com o caminho do Inno Setup 6 ISCC.exe")
    provenance = source_info(catalog.sources)
    if expected_revision and provenance["revision"] != str(expected_revision):
        raise ValueError(f"Revisão ACBr incompatível: esperado {expected_revision}, encontrado {provenance['revision']}")
    if expected_commit and provenance.get("commit") != expected_commit:
        raise ValueError("SHA Git dos fontes não corresponde ao commit selecionado")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # A new directory, even after a failure, prevents reusing stale binaries.
    directory = output / f"build-{time.time_ns()}-{os.getpid()}"
    directory.mkdir()
    entries = describe(jobs, directory, tool, pcp, rebuild)
    report = {"generator_version": __version__, "source": provenance, "host": platform.platform(),
              "metadata": metadata or {}, "status": "running", "skipped": skipped,
              "jobs": [{**entry, "status": "pending", "files": []} for entry in entries]}
    manifest = directory / "manifest.json"
    runner = runner_factory(directory / "build.log")
    runner.say(f"ACBrLibGenerator {__version__}; {len(jobs)} compilações; saída: {directory}")
    for note in skipped:
        runner.say(f"Ignorado: {note}")
    write_json(manifest, report)
    try:
        for job, entry in zip(jobs, report["jobs"]):
            entry["status"] = "running"
            write_json(manifest, report)
            target = Path(entry["directory"])
            (target / "units").mkdir(parents=True)
            binary = Path(entry["binary"])
            runner.run(entry["command"], cwd=job.project.path.parent)
            if not binary.is_file() or not binary.stat().st_size:
                raise RuntimeError(f"Compilação terminou sem gerar {binary}")
            files = [binary, archive(job, binary, target, catalog.sources)]
            if job.installer:
                files.extend(installer.build(runner, job.project, job.mode, binary, target, iscc))
            entry["files"] = [{"path": f.relative_to(directory).as_posix(), "sha256": sha256(f), "size": f.stat().st_size} for f in files]
            entry["status"] = "success"
            runner.say(f"Gerado: {binary}")
            write_json(manifest, report)
        report["status"] = "success"
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        for entry in report["jobs"]:
            if entry["status"] == "running":
                entry["status"] = "failed"
        runner.say(f"ERRO: {report['error']}")
        raise
    finally:
        write_json(manifest, report)
        sums = [f"{f['sha256']}  {f['path']}" for entry in report["jobs"] for f in entry["files"]]
        (directory / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return directory
