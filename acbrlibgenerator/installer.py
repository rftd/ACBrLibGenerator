"""Prepare a private copy of the official Monitor installer script."""

from pathlib import Path
import re


def prepare(original: bytes, sources: Path, monitor: Path, binary: Path, x64: bool) -> bytes:
    try:
        text = original.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = original.decode("cp1252")
    paths = [str(p).replace("/", "\\") for p in (sources, monitor, binary)]
    if any(any(c in path for c in '"\r\n') for path in paths):
        raise ValueError("Caminho não suportado pelo Inno Setup")
    root, source_dir, exe = paths
    text, roots = re.subn(r'^\s*#define ACBrDIR[^\S\r\n]+[^\r\n]*', '', text, flags=re.M)
    text = re.sub(r'^\s*#define MyAppTarget[^\S\r\n]+[^\r\n]*', '', text, flags=re.M)
    text = re.sub(r'^SourceDir=[^\r\n]*', '', text, flags=re.M)
    text, versions = re.subn(r'^#define MyAppVersion GetFileVersion\(MyAppExeName\)\s*$',
                            '#define MyAppVersion GetFileVersion(BuilderBinary)', text, flags=re.M)
    text, binaries = re.subn(r'^Source: \{#MyAppExeName\};', 'Source: "{#BuilderBinary}";', text, flags=re.M)
    # Use a lambda so backslashes in Windows paths are never regex escapes.
    text, sections = re.subn(r'^\[Setup\]\s*$', lambda _: f'[Setup]\nSourceDir={source_dir}', text, flags=re.M)
    if (roots, versions, binaries, sections) != (1, 1, 1, 1):
        raise ValueError(f"Estrutura do script oficial mudou: ACBrDIR/versão/binário/Setup={roots}/{versions}/{binaries}/{sections}")
    header = (f'; Cópia gerada pelo ACBrLibGenerator\n#define ACBrDIR "{root}"\n'
              f'#define MyAppTarget "{"x64" if x64 else "x86"}"\n#define BuilderBinary "{exe}"\n')
    return (header + text).replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8-sig")


def build(runner, project, mode, binary: Path, directory: Path, iscc: Path):
    script = project.path.parent / "ACBrMonitorPLUS.iss"
    sources = project.path.parents[3]
    staged = directory / "ACBrMonitorPLUS.iss"
    staged.write_bytes(prepare(script.read_bytes(), sources, project.path.parent, binary, mode.os == "win64"))
    output = directory / "installer"
    output.mkdir()
    runner.run([iscc, f"/O{output}", staged], cwd=project.path.parent)
    generated = sorted(p for p in output.glob("*.exe") if p.is_file() and p.stat().st_size)
    if not generated:
        raise RuntimeError(f"Inno Setup terminou sem gerar instalador em {output}")
    return generated
