"""Small compatibility fixes applied only to the verified CI source checkout."""
import re
from .common import sha256


def apply_compatibility(sources, runner):
    relative = 'Projetos/ACBrLib/Fontes/DIS/ACBrLibDISBase.pas'
    path = sources / relative
    if not path.is_file():
        return []
    original = path.read_bytes()
    replacements = {b'ConverterUTF8ParaAnsi': b'ConverterStringSaida',
                    b'ConverterAnsiParaUTF8': b'ConverterStringEntrada'}
    needed = {old: new for old, new in replacements.items()
              if re.search(rb'\b' + old + rb'\s*\(', original)}
    if not needed:
        return []
    common = (sources / 'Projetos/ACBrLib/Fontes/Comum/ACBrLibComum.pas').read_bytes()
    updated = original
    for old, new in needed.items():
        signature = rb'\bfunction\s+(?:TACBrLib\.)?'
        if re.search(signature + old + rb'\b', common, re.I):
            continue  # Older revisions still provide these methods.
        if not re.search(signature + new + rb'\b', common, re.I):
            raise ValueError(f'ACBrLibDIS: método de conversão compatível ausente: {new.decode()}')
        updated = re.sub(rb'\b' + old + rb'(?=\s*\()', new, updated)
    if updated == original:
        return []
    before = sha256(path)
    # Byte replacements preserve the upstream BOM, encoding and line endings.
    path.write_bytes(updated)
    patch = {'id': 'acbrlib-dis-string-conversion', 'file': relative,
             'original_sha256': before, 'patched_sha256': sha256(path)}
    runner.say(f'Compatibilidade: {relative}: usar ConverterStringEntrada/ConverterStringSaida da TACBrLib.')
    return [patch]
