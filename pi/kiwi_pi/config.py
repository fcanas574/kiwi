"""Carga de config.yaml con valores por defecto y ajustes guardados en tiempo de ejecución."""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PI_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PI_DIR / 'config.yaml'
EXAMPLE_CONFIG = PI_DIR / 'config.example.yaml'


@dataclass(frozen=True)
class Slot:
    id: int
    category: str
    label: str
    location: str

    def as_dict(self) -> dict[str, Any]:
        return {'id': self.id, 'category': self.category, 'label': self.label, 'location': self.location}


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """Configuración de la estación. `data_dir` guarda ajustes de la pantalla, fotos y retroalimentación."""

    def __init__(self, raw: dict, data_dir: Path, scan_pairing_key: str = ''):
        self.raw = raw
        self.data_dir = data_dir
        # Keep the phone pairing secret out of the YAML payload and every
        # general-purpose config/status serialization path.
        self.scan_pairing_key = scan_pairing_key
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = data_dir / 'settings.json'
        settings = self._load_settings()
        if settings.get('recognizer_url'):
            raw['recognizer']['url'] = settings['recognizer_url']
        if settings.get('scan_mode') in ('single', 'tracked'):
            raw.setdefault('scan', {})['mode'] = settings['scan_mode']

        self.slots = [Slot(int(s['id']), str(s['category']), str(s['label']), str(s.get('location') or f"Posición {s['id']}"))
                      for s in raw['station']['slots']]
        ids = [s.id for s in self.slots]
        if len(set(ids)) != len(ids):
            raise ValueError('Los id de station.slots deben ser únicos.')
        categories = [s.category for s in self.slots]
        if len(set(categories)) != len(categories):
            raise ValueError('Cada categoría debe aparecer una sola vez en station.slots.')
        if len(self.slots) != 9:
            print(f'Aviso: el reto pide 9 herramientas y la estación tiene {len(self.slots)}.')

    def section(self, name: str) -> dict:
        return self.raw.get(name, {})

    def slot_for(self, category: str | None) -> Slot | None:
        return next((s for s in self.slots if s.category == category), None)

    def slot_by_id(self, slot_id: int) -> Slot | None:
        return next((s for s in self.slots if s.id == slot_id), None)

    def slot_index(self, slot_id: int) -> int:
        """Índice 0..n-1 usado por los drivers (orden de station.slots)."""
        return next(i for i, s in enumerate(self.slots) if s.id == slot_id)

    def _load_settings(self) -> dict:
        try:
            return json.loads(self._settings_path.read_text())
        except (OSError, ValueError):
            return {}

    def save_setting(self, key: str, value: Any) -> None:
        settings = self._load_settings()
        settings[key] = value
        self._settings_path.write_text(json.dumps(settings, indent=2))


def load_config(path: str | os.PathLike | None = None, overrides: dict | None = None) -> Config:
    defaults = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    path = Path(path) if path else (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None)
    user = yaml.safe_load(path.read_text()) if path else {}
    raw = _merge(_merge(defaults, user or {}), overrides or {})

    env_key = os.environ.get('KIWI_API_KEY')
    if env_key:
        raw['recognizer']['api_key'] = env_key
    env_pass = os.environ.get('KIWI_GRADIO_PASSWORD')
    if env_pass:
        raw['recognizer']['gradio_password'] = env_pass
    scan_pairing_key = os.environ.get('KIWI_SCAN_PAIRING_KEY', '')

    data_dir = Path(os.environ.get('KIWI_DATA_DIR', PI_DIR / 'data'))
    return Config(raw, data_dir, scan_pairing_key)
