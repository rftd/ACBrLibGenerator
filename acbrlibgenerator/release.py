"""Assemble FinalBuilder-style library ZIPs and Windows Monitor installers."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile
from collections import defaultdict

from .catalog import Catalog
from .common import sha256, write_json
from .sources import verify

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r'^\d+\.\d+\.\d+\.\d+$')
INSTALLER = re.compile(r'^ACBrMonitorPLUS-\d+\.\d+\.\d+\.\d+-(x86|x64)-Instalador\.exe$', re.I)


def contained(root, relative):
    path = PurePosixPath(str(relative).replace('\\','/'))
    if path.is_absolute() or '..' in path.parts or ':' in str(path):
        raise ValueError(f'Caminho inválido no pacote: {relative}')
    result = root.joinpath(*path.parts).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError(f'Caminho fora da pasta: {relative}')
    return result


def collect(artifacts, sources, lock, output, templates=ROOT/'ACBrLib', recipes=None):
    verify(sources,lock)
    if output.exists() and any(output.iterdir()):
        raise ValueError('A pasta de publicação deve estar vazia')
    recipes = recipes if recipes is not None else json.loads((ROOT/'config/distribution.json').read_text())
    groups=defaultdict(list)
    installers={}
    monitor_targets=set()
    for manifest in sorted(artifacts.rglob('manifest.json')):
        report=json.loads(manifest.read_text())
        if report['status']!='success':
            raise ValueError(f'Build incompleto: {manifest}')
        if (report['source'].get('commit'),str(report['source']['revision'])) != (lock['commit'],str(lock['revision'])):
            raise ValueError('Artefatos de revisões diferentes não podem ser publicados juntos')
        for job in report['jobs']:
            if job['status']!='success':raise ValueError('Compilação incompleta no manifesto')
            files=[]
            for entry in job['files']:
                path=contained(manifest.parent,entry['path'])
                # Raw binaries need not be uploaded; their ZIP is sufficient for publication.
                if path.suffix.casefold() not in {'.zip','.exe'} or not path.exists():continue
                if sha256(path)!=entry['sha256']:raise ValueError(f'Checksum inválido: {path}')
                files.append(path)
            if job.get('library'):
                package=next((p for p in files if p.suffix=='.zip'),None)
                if package is None:raise ValueError(f'ZIP ausente: {job["project"]} {job["mode"]}')
                groups[job['project']].append((job,package))
            elif job['project']=='ACBrMonitor' and job['installer']:
                target=job['target']
                found=[p for p in files if INSTALLER.fullmatch(p.name)]
                if len(found)!=1:raise ValueError(f'Instalador Windows ausente ou duplicado: {target}')
                if target in monitor_targets:raise ValueError(f'Instalador duplicado: {target}')
                monitor_targets.add(target)
                installers[found[0].name]=found[0]
    expected={p.name for p in Catalog.load(sources).libraries}
    if set(groups)!=expected:
        raise ValueError(f'Bibliotecas ausentes ou inesperadas: {sorted(expected-set(groups))} / {sorted(set(groups)-expected)}')
    if monitor_targets!={'i386-win32','x86_64-win64'}:
        raise ValueError('A release exige os instaladores x86 e x64 do Monitor Windows')
    output.mkdir(parents=True,exist_ok=True)
    published=[]
    for name, entries in sorted(groups.items()):
        binary_versions={j['file_version'] for j,_ in entries if j.get('version_source')=='binary'}
        if len(binary_versions)!=1:
            raise ValueError(f'FileVersion Windows ausente ou inconsistente: {name}')
        version=next(iter(binary_versions))
        if not VERSION.fullmatch(version):raise ValueError('FileVersion inválida')
        destination=output/f'{name} {version}.zip'
        members={}

        def add_file(path, relative):
            if path.suffix.casefold()==".bat":return
            key=PurePosixPath(str(relative).replace('\\','/')).as_posix()
            contained(output,key)
            if key in members:
                if members[key]!=path.read_bytes():raise ValueError(f'Conteúdo conflitante no ZIP: {name}/{key}')
            else:members[key]=path.read_bytes()

        recipe=recipes.get(name,{})
        dynamic_files=set(recipe.get("files",{}))
        dynamic_directories=tuple(recipe.get("directories",{}))
        template=templates/name
        if not template.is_dir():template=templates/'_common'
        if not template.is_dir():raise ValueError(f'Modelo de distribuição ausente: {name}')
        for path in sorted(template.rglob('*')):
            if path.is_file() and not path.is_symlink():
                relative=path.relative_to(template)
                if any(part.casefold() in {'bin','log','logs'} for part in relative.parts) or path.suffix.casefold() in {'.bat','.fbl8'}:continue
                rel=relative.as_posix()
                if rel in dynamic_files or any(rel.startswith(d+'/') for d in dynamic_directories):continue
                add_file(path,rel)
        for job,package in entries:
            binary_name=job['archive_path']
            with zipfile.ZipFile(package) as archive:
                if binary_name not in archive.namelist():raise ValueError(f'Binário ausente no ZIP: {binary_name}')
                contained(output,binary_name)
                data=archive.read(binary_name)
                if binary_name in members:raise ValueError(f'Binário duplicado: {binary_name}')
                members[binary_name]=data
        defaults={f'log/{name}-change-log.txt':f'Projetos/ACBrLib/Fontes/{name.removeprefix("ACBrLib")}/{name}-change-log.txt',
                  'log/ACBrLibComum-change-log.txt':'Projetos/ACBrLib/Fontes/Comum/ACBrLibComum-change-log.txt'}
        if name not in recipes:
            component_name=name.removeprefix('ACBrLib')
            services=sources/f'Fontes/ACBrDFe/ACBr{component_name}/ACBr{component_name}Servicos.ini'
            if services.is_file():defaults[f'dep/{services.name}']=services.relative_to(sources).as_posix()
        for relative,source in {**defaults,**recipe.get('files',{})}.items():
            path=contained(sources,source)
            if not path.is_file():raise ValueError(f'Arquivo de distribuição ausente nos fontes: {source}')
            add_file(path,relative)
        directories=dict(recipe.get('directories',{}))
        schema=sources/'Exemplos/ACBrDFe/Schemas'/name.removeprefix('ACBrLib')
        if schema.is_dir():directories.setdefault(f'dep/Schemas/{schema.name}',schema.relative_to(sources).as_posix())
        for relative,source in directories.items():
            folder=contained(sources,source)
            if not folder.is_dir():raise ValueError(f'Pasta de distribuição ausente: {source}')
            for path in sorted(folder.rglob('*')):
                if path.is_file() and not path.is_symlink():add_file(path,f'{relative}/{path.relative_to(folder).as_posix()}')
        if 'LEIA-ME.pdf' not in members:raise ValueError(f'LEIA-ME.pdf ausente: {name}')
        with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for relative,data in sorted(members.items()):archive.writestr(relative,data)
        published.append({'name':destination.name,'sha256':sha256(destination),'file_version':version,'library':name,
                          'modes':[j['mode'] for j,_ in entries]})
    for name,path in sorted(installers.items()):
        destination=output/name
        shutil.copy2(path,destination)
        published.append({'name':name,'sha256':sha256(destination)})
    write_json(output/'source-lock.json',lock)
    write_json(output/'release-manifest.json',{'source':lock,'files':published})
    (output/'SHA256SUMS').write_text(''.join(f"{p['sha256']}  {p['name']}\n" for p in published),encoding='utf-8')
    return published


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts',type=Path,required=True)
    parser.add_argument('--sources',type=Path,required=True)
    parser.add_argument('--lock',type=Path,default=Path('source-lock.json'))
    parser.add_argument('--output',type=Path,default=Path('publish'))
    args=parser.parse_args()
    collect(args.artifacts,args.sources,json.loads(args.lock.read_text()),args.output)
    return 0
