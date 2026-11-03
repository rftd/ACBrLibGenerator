"""Read the upstream projects instead of maintaining a second build-mode list."""

from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
import xml.etree.ElementTree as ET
import re


@dataclass(frozen=True)
class Mode:
    name: str
    cpu: str
    os: str
    filename: str


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    library: bool
    modes: tuple[Mode, ...]
    packages: tuple[str, ...]
    version: str

    def describe(self):
        return {**asdict(self), "path": str(self.path)}


def xml(path: Path):
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    return ET.fromstring(text)


def project(path: Path, library: bool) -> Project:
    root = xml(path)
    base = root.find("CompilerOptions")
    modes_node = root.find("ProjectOptions/BuildModes")
    modes = []
    for item in modes_node if modes_node is not None else []:
        name = item.get("Name", "")
        if "debug" in name.casefold() or (not library and not name.startswith("Release-")):
            continue
        options = item.find("CompilerOptions")

        def value(key, default=""):
            for source in (options, base):
                node = source.find(key) if source is not None else None
                if node is not None and node.get("Value") is not None:
                    return node.get("Value")
            return default

        cpu = value("CodeGeneration/TargetCPU")
        os_name = value("CodeGeneration/TargetOS")
        # Older GAV/LCB projects declare Windows calling conventions but omit OS.
        legacy = re.fullmatch(r'x(86|64) - (CDECL|STDCALL)', name, flags=re.I)
        if not os_name and legacy:
            cpu = cpu or ('i386' if legacy[1] == '86' else 'x86_64')
            os_name = 'win32' if cpu == 'i386' else 'win64'
        if cpu not in {"i386", "x86_64"} or os_name not in {"win32", "win64", "linux"}:
            continue
        target = value("Target/Filename").replace("\\", "/")
        filename = target.rsplit("/", 1)[-1]
        if not filename or filename in {".", ".."} or any(c in filename for c in '$"\r\n'):
            raise ValueError(f"Nome de saída inválido em {path}: {target!r}")
        suffix = (".dll" if library else ".exe") if os_name.startswith("win") else (".so" if library else "")
        if suffix and not filename.casefold().endswith(suffix):
            filename += suffix
        modes.append(Mode(name, cpu, os_name, filename))
    version = root.find("ProjectOptions/VersionInfo/StringTable")
    version = version.get("ProductVersion", "") if version is not None else ""
    packages = tuple(n.get("Value") for n in root.findall("ProjectOptions/RequiredPackages/*/PackageName") if n.get("Value"))
    return Project(path.stem, path.resolve(), library, tuple(modes), packages, version)


@dataclass(frozen=True)
class Catalog:
    sources: Path
    libraries: tuple[Project, ...]
    monitor: Project | None

    @classmethod
    def load(cls, sources: Path):
        sources = sources.resolve(strict=True)
        libraries = []
        folder = sources / "Projetos/ACBrLib/Fontes"
        if folder.is_dir():
            for path in folder.glob("*/*.lpi"):
                # Exclude demos and ConsoleMT projects; ST/MT are modes of the main project.
                if path.stem.casefold() == f"ACBrLib{path.parent.name}".casefold():
                    libraries.append(project(path, True))
        libraries.sort(key=lambda p: p.name.casefold())
        monitor_path = sources / "Projetos/ACBrMonitorPLUS/Lazarus/ACBrMonitor.lpi"
        monitor = project(monitor_path, False) if monitor_path.is_file() else None
        if not libraries and monitor is None:
            raise ValueError("Nenhum projeto encontrado. Informe a raiz ACBr que contém a pasta Projetos.")
        return cls(sources, tuple(libraries), monitor)


def relative_binary_path(project: Project, mode: Mode) -> Path:
    """Preserve the upstream bin layout for archive consumers."""
    root = xml(project.path)
    item = next(n for n in root.find("ProjectOptions/BuildModes") if n.get("Name") == mode.name)
    node = item.find("CompilerOptions/Target/Filename")
    if node is None:
        node = root.find("CompilerOptions/Target/Filename")
    target = node.get("Value", "") if node is not None else ""
    path = PureWindowsPath(target)
    if path.is_absolute() or ".." in path.parts:
        # An out-of-project mode is never used as an archive path.
        return Path(mode.filename)
    return Path(*path.parts[:-1]) / mode.filename
