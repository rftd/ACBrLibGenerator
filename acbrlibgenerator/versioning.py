"""Read the numeric FileVersion resource used by the original FinalBuilder job."""
from pathlib import Path
import struct
from .catalog import xml


def file_version(binary: Path) -> str:
    data = binary.read_bytes()

    def unpack(fmt, offset):
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(data):
            raise ValueError(f'Recurso de versão truncado: {binary}')
        return struct.unpack_from(fmt, data, offset)

    if data[:2] != b'MZ':
        raise ValueError(f'Binário Windows inválido: {binary}')
    pe = unpack('<I', 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError(f'Assinatura PE inválida: {binary}')
    count = unpack('<H', pe + 6)[0]
    optional_size = unpack('<H', pe + 20)[0]
    optional = pe + 24
    magic = unpack('<H', optional)[0]
    if magic not in (0x10b, 0x20b):
        raise ValueError('Formato PE não suportado')
    directories = optional + (96 if magic == 0x10b else 112)
    resource_rva, resource_size = unpack('<II', directories + 16)
    sections = []
    for index in range(count):
        section = optional + optional_size + index * 40
        virtual_size, address, raw_size, raw = unpack('<IIII', section + 8)
        sections.append((address, max(virtual_size, raw_size), raw, raw_size))

    def offset(rva):
        for address, size, raw, raw_size in sections:
            if address <= rva < address + size and rva - address < raw_size:
                return raw + rva - address
        raise ValueError(f'RVA de versão inválido: {binary}')

    if not resource_rva or not resource_size:
        raise ValueError(f'Binário sem recurso FileVersion: {binary}')
    base = offset(resource_rva)

    def entries(relative):
        directory = base + relative
        named, ids = unpack('<HH', directory + 12)
        if named + ids > 65535:
            raise ValueError('Diretório PE inválido')
        return [unpack('<II', directory + 16 + i * 8) for i in range(named + ids)]

    versions = [target for identifier, target in entries(0) if identifier == 16]
    if not versions:
        raise ValueError(f'FileVersion não encontrado: {binary}')
    target = versions[0]
    for _ in range(4):
        if not target & 0x80000000:
            break
        children = entries(target & 0x7fffffff)
        if not children:
            raise ValueError('Diretório de versão vazio')
        target = children[0][1]
    if target & 0x80000000:
        raise ValueError('Diretório de versão com profundidade inválida')
    value_rva, length = unpack('<II', base + target)
    start = offset(value_rva)
    if start + length > len(data):
        raise ValueError('Recurso de versão fora do arquivo')
    key = 'VS_VERSION_INFO\0'.encode('utf-16le')
    if data[start + 6:start + 6 + len(key)] != key:
        raise ValueError('Recurso VS_VERSION_INFO inválido')
    fixed = start + ((6 + len(key) + 3) & ~3)
    signature, _, ms, ls = unpack('<IIII', fixed)
    if signature != 0xfeef04bd:
        raise ValueError('VS_FIXEDFILEINFO inválido')
    return f'{ms >> 16}.{ms & 0xffff}.{ls >> 16}.{ls & 0xffff}'


def project_file_version(project) -> str:
    root = xml(project.path)
    info = root.find('ProjectOptions/VersionInfo')
    if info is None:
        raise ValueError(f'Projeto sem VersionInfo: {project.path}')
    values = []
    for name in ('MajorVersionNr', 'MinorVersionNr', 'RevisionNr', 'BuildNr'):
        node = info.find(name)
        values.append(str(int(node.get('Value', '0'))) if node is not None else '0')
    return '.'.join(values)
