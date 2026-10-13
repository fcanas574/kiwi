"""Incorpora las correcciones de "No es correcta" a las referencias del modelo.

Uso: `python -m kiwi_pi.dataset [--feedback data/feedback] [--dataset ../dataset_prepared]`

Copia data/feedback/<categoría>/*.jpg a dataset_prepared/references/<categoría>/, añade filas a
manifest.csv, regenera references.zip y mueve lo importado a data/feedback/_importado/. Después hay
que subir el ZIP a Drive y ejecutar de nuevo las secciones 4 y 5 del notebook.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import zipfile
from datetime import date
from pathlib import Path

from .config import PI_DIR, load_config

IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}


def next_index(folder: Path, category: str) -> int:
    numbers = [int(m.group(1)) for p in folder.glob(f'{category}_*')
               if (m := re.fullmatch(rf'{re.escape(category)}_(\d+)', p.stem))]
    return max(numbers, default=0) + 1


def confirmed(photo: Path, category: str) -> bool:
    """Una foto con .json solo se importa si la persona eligió su etiqueta real en "¿Qué era?".

    Las fotos sin .json se consideran colocadas a mano en la carpeta correcta.
    """
    sidecar = photo.with_suffix('.json')
    if not sidecar.exists():
        return True
    try:
        return json.loads(sidecar.read_text()).get('actual') == category
    except ValueError:
        return False


def build_zip(dataset: Path) -> tuple[Path, int]:
    target = dataset / 'references.zip'
    files = sorted(p for p in (dataset / 'references').rglob('*')
                   if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(dataset).as_posix())
    return target, len(files)


def import_feedback(feedback: Path, dataset: Path) -> dict[str, int]:
    references = dataset / 'references'
    known = {p.name for p in references.iterdir() if p.is_dir()}
    imported: dict[str, int] = {}
    rows = []
    for folder in sorted(p for p in feedback.iterdir() if p.is_dir()):
        category = folder.name
        if category.startswith('_') or category == 'sin_etiqueta':
            continue
        if category not in known:
            print(f'Se omite {category}: no existe references/{category} (¿falta en CATEGORIES?).')
            continue
        destination = references / category
        archive = feedback / '_importado' / category
        archive.mkdir(parents=True, exist_ok=True)
        for photo in sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES):
            if not confirmed(photo, category):
                print(f'Se omite {photo.name}: no tiene la etiqueta real confirmada en la pantalla.')
                continue
            name = f'{category}_{next_index(destination, category):03d}.jpg'
            shutil.copy2(photo, destination / name)
            rows.append(f'Corrección en la estación {date.today()},dataset_prepared/references/{category}/{name},'
                        f'{category},high,"Foto marcada con No es correcta ({photo.name})."\n')
            for sidecar in (photo, photo.with_suffix('.json')):
                if sidecar.exists():
                    shutil.move(str(sidecar), archive / sidecar.name)
            imported[category] = imported.get(category, 0) + 1
    if rows:
        with (dataset / 'manifest.csv').open('a', encoding='utf-8', newline='\r\n') as manifest:   # el CSV usa CRLF
            manifest.writelines(rows)
    return imported


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(description='Incorporar correcciones a las referencias del modelo')
    parser.add_argument('--feedback', type=Path, default=cfg.data_dir / 'feedback')
    parser.add_argument('--dataset', type=Path, default=PI_DIR.parent / 'dataset_prepared')
    args = parser.parse_args()

    if not args.feedback.is_dir():
        print(f'No hay correcciones en {args.feedback}.')
        return
    imported = import_feedback(args.feedback, args.dataset)
    if not imported:
        print('No había fotos nuevas con etiqueta para importar.')
        return
    target, total = build_zip(args.dataset)
    print('Importadas:', ', '.join(f'{k}: {v}' for k, v in imported.items()))
    print(f'{target} regenerado con {total} fotos.')
    print('Siguiente paso: subir el ZIP a Drive y ejecutar de nuevo las secciones 4 y 5 del notebook.')


if __name__ == '__main__':
    main()
