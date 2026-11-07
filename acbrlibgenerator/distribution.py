"""The release scope imported from FinalBuilder project assignments."""
import json
from pathlib import Path


def release_libraries():
    path = Path(__file__).resolve().parents[1] / 'config/release-libraries.json'
    names = json.loads(path.read_text(encoding='utf-8'))['libraries']
    if not names or len(set(name.casefold() for name in names)) != len(names):
        raise ValueError('Lista de bibliotecas do FinalBuilder vazia ou duplicada')
    return tuple(names)
