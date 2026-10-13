"""Clientes del modelo de reconocimiento (Colab) y la decisión final de la estación."""
from __future__ import annotations

import asyncio
import random
import io
import math
from PIL import Image
import tempfile
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Config


class RecognizerError(RuntimeError):
    pass


@dataclass
class Health:
    online: bool
    detail: str = ''
    mode: str | None = None
    calibrated: bool | None = None


class Recognizer:
    name = 'base'

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.section = cfg.section('recognizer')

    @property
    def url(self) -> str:
        return self.section.get('url', '')

    def set_url(self, url: str) -> None:
        self.section['url'] = url

    async def predict(self, jpeg: bytes) -> dict:
        raise NotImplementedError

    async def health(self) -> Health:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class RestRecognizer(Recognizer):
    """API REST de la sección 6b del notebook: POST /predict (multipart `file`) y GET /health."""
    name = 'rest'

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.client = httpx.AsyncClient(timeout=float(self.section.get('timeout_seconds', 30)))

    def _headers(self) -> dict[str, str]:
        headers = {'ngrok-skip-browser-warning': '1'}
        if self.section.get('api_key'):
            headers['Authorization'] = f"Bearer {self.section['api_key']}"
        return headers

    def _health_url(self) -> str:
        url = self.url.rstrip('/')
        return (url[: -len('/predict')] if url.endswith('/predict') else url) + '/health'

    def _configured(self) -> bool:
        return self.url.startswith(('http://', 'https://')) and 'TU-URL' not in self.url

    async def predict(self, jpeg: bytes) -> dict:
        if not self._configured():
            raise RecognizerError('Configura la URL del endpoint de Colab.')
        try:
            response = await self.client.post(self.url, headers=self._headers(),
                                              files={'file': ('foto.jpg', jpeg, 'image/jpeg')})
        except httpx.HTTPError as exc:
            raise RecognizerError(f'No se pudo contactar la API: {exc.__class__.__name__}') from exc
        if response.status_code == 401:
            raise RecognizerError('La API rechazó la clave (KIWI_API_KEY).')
        if response.status_code >= 400:
            raise RecognizerError(f'La API respondió {response.status_code}.')
        try:
            return response.json()
        except ValueError as exc:
            raise RecognizerError('La API no devolvió JSON. ¿El enlace de ngrok sigue activo?') from exc

    async def health(self) -> Health:
        if not self._configured():
            return Health(False, 'Sin URL configurada')
        try:
            response = await self.client.get(self._health_url(), headers=self._headers(), timeout=8)
            data = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            return Health(False, exc.__class__.__name__)
        if not data.get('ok'):
            return Health(False, f'HTTP {response.status_code}')
        return Health(True, 'OK', data.get('mode'), data.get('calibrated'))

    async def close(self) -> None:
        await self.client.aclose()


class GradioRecognizer(Recognizer):
    """Enlace gradio.live de la sección 6 del notebook (api_name=/identify)."""
    name = 'gradio'

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self._client = None
        self._client_url = None

    def _get_client(self):
        try:
            from gradio_client import Client
        except ImportError as exc:
            raise RecognizerError('Instala gradio_client para usar el driver gradio.') from exc
        base = self.url.rstrip('/')
        if self._client is None or self._client_url != base:
            auth = (self.section.get('gradio_user', 'kiwi'), self.section.get('gradio_password', ''))
            self._client = Client(base, auth=auth if auth[1] else None, verbose=False)
            self._client_url = base
        return self._client

    def _predict_sync(self, jpeg: bytes) -> dict:
        from gradio_client import handle_file
        with tempfile.NamedTemporaryFile(suffix='.jpg') as tmp:
            tmp.write(jpeg)
            tmp.flush()
            result = self._get_client().predict(handle_file(tmp.name), api_name='/identify')
            if not isinstance(result, (list, tuple)) or len(result) != 3 or not isinstance(result[2], dict):
                raise RecognizerError('Gradio no devolvió los resultados multiobjeto. Actualiza el notebook.')
            return result[2]

    async def predict(self, jpeg: bytes) -> dict:
        try:
            return await asyncio.to_thread(self._predict_sync, jpeg)
        except RecognizerError:
            raise
        except Exception as exc:  # gradio_client lanza varios tipos
            self._client = None
            raise RecognizerError(f'Error de Gradio: {exc}') from exc

    async def health(self) -> Health:
        if 'gradio' not in self.url:
            return Health(False, 'Sin URL de Gradio')
        try:
            await asyncio.to_thread(self._get_client)
            return Health(True, 'OK')
        except Exception as exc:
            self._client = None
            return Health(False, str(exc)[:80])


class MockRecognizer(Recognizer):
    """Simula la API: elige una categoría de la estación (o la que se fije con `force`)."""
    name = 'mock'
    force: str | None = None

    async def predict(self, jpeg: bytes) -> dict:
        await asyncio.sleep(0.1)
        with Image.open(io.BytesIO(jpeg)) as image:
            width, height = image.size
        categories = self.force.split(',') if self.force else ['digital_multimeter', 'servo_motor', 'solder']
        objects = []
        for i, category in enumerate(categories[:12], 1):
            x1 = int((i - 1) * width / len(categories))
            x2 = max(x1 + 1, int(i * width / len(categories)))
            box = [x1, 0, x2, height]
            accepted = category != 'otros'
            result = {'candidate': category, 'recognized': accepted, 'similarity': .95, 'margin': .75,
                      'reason': 'Simulación (driver mock).', 'mode': 'mock',
                      'ranking': [{'object': category, 'similarity': .95}]}
            objects.append({'id': i, 'box': box, 'crop_box': box, 'category': category,
                            'recognized': accepted, 'detector_label': category, 'detector_score': .5,
                            'recognition': result})
        return {'mode': 'grounding_dino_qwen', 'image_size': [width, height], 'objects': objects}

    async def health(self) -> Health:
        return Health(True, 'Simulación', 'mock', True)


def make_recognizer(cfg: Config) -> Recognizer:
    driver = cfg.section('recognizer').get('driver', 'rest')
    return {'rest': RestRecognizer, 'gradio': GradioRecognizer, 'mock': MockRecognizer}[driver](cfg)


@dataclass
class Decision:
    accepted: bool
    category: str | None
    similarity: float | None
    margin: float | None
    reason: str
    test_mode: bool = False
    mode: str | None = None
    ranking: list[dict[str, Any]] = field(default_factory=list)


def decide(cfg: Config, api: dict) -> Decision:
    """Decide si se enciende un LED. Solo las categorías de la estación cuentan para el margen."""
    rules = cfg.section('decision')
    station = {s.category for s in cfg.slots}
    ranking = []
    for item in api.get('ranking') or []:
        try:
            ranking.append({'object': str(item['object']), 'similarity': float(item['similarity'])})
        except (KeyError, TypeError, ValueError):
            continue
    ranking.sort(key=lambda item: item['similarity'], reverse=True)
    if not ranking and api.get('candidate') is not None and api.get('similarity') is not None:
        ranking = [{'object': api['candidate'], 'similarity': float(api['similarity'])}]

    candidate = api.get('candidate')
    similarity = api.get('similarity')
    margin = api.get('margin')
    common = {'mode': api.get('mode'), 'ranking': ranking[:5]}

    if candidate is None:
        return Decision(False, None, None, None, api.get('reason') or 'La API no devolvió candidato.', **common)
    if candidate not in station:
        reason = 'El objeto no pertenece a las herramientas de esta estación.'
        if candidate == 'otros' and api.get('reason'):
            reason = api['reason']   # el VLM explica si no es una herramienta o si no se ve su frente
        return Decision(False, candidate, similarity, margin, reason, **common)

    min_similarity, min_margin = rules.get('min_similarity'), rules.get('min_margin')
    if min_similarity is not None and min_margin is not None and ranking:
        in_station = [r for r in ranking if r['object'] in station]
        similarity = in_station[0]['similarity']
        margin = similarity - in_station[1]['similarity'] if len(in_station) > 1 else None
        if in_station[0]['object'] != candidate:
            return Decision(False, candidate, similarity, margin, 'Resultado inconsistente de la API.', **common)
        if similarity < float(min_similarity):
            return Decision(False, candidate, similarity, margin,
                            'Similitud insuficiente: objeto desconocido o foto poco clara.', **common)
        if margin is not None and margin < float(min_margin):
            return Decision(False, candidate, similarity, margin,
                            'Dos herramientas se parecen demasiado. Repite la foto.', **common)
        return Decision(True, candidate, similarity, margin, 'Aceptado con los umbrales de la estación.', **common)

    if api.get('recognized'):
        return Decision(True, candidate, similarity, margin, api.get('reason') or 'Reconocido por la API.', **common)
    if rules.get('test_mode'):
        return Decision(True, candidate, similarity, margin, 'Modo prueba: umbrales sin calibrar.',
                        test_mode=True, **common)
    return Decision(False, candidate, similarity, margin, api.get('reason') or 'La API no confirmó el objeto.', **common)


def validate_objects(api: dict, image_size: tuple[int, int]) -> list[dict]:
    """Reject malformed results before lighting LEDs or using crop coordinates."""
    def fail():
        raise RecognizerError('La API no devolvió una lista multiobjeto válida. Actualiza el notebook y repite la foto.')
    if not isinstance(api, dict) or api.get('image_size') != list(image_size):
        fail()
    objects = api.get('objects')
    if not isinstance(objects, list) or len(objects) > 12:
        fail()
    seen = set()
    width, height = image_size
    for item in objects:
        if not isinstance(item, dict): fail()
        id = item.get('id')
        if type(id) is not int or id < 1 or id in seen: fail()
        seen.add(id)
        for name in ('box', 'crop_box'):
            box = item.get(name)
            if not isinstance(box, list) or len(box) != 4: fail()
            if not all(type(v) in (int, float) and math.isfinite(v) for v in box): fail()
            x1, y1, x2, y2 = box
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height): fail()
        r = item.get('recognition')
        if not isinstance(r, dict) or type(item.get('recognized')) is not bool or type(r.get('recognized')) is not bool: fail()
        if item['recognized'] != r['recognized'] or not isinstance(item.get('category'), str) or item['category'] != r.get('candidate'): fail()
        for value in (item.get('detector_score'), r.get('similarity'), r.get('margin')):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1: fail()
        ranking = r.get('ranking')
        if not isinstance(ranking, list): fail()
        for entry in ranking:
            if not isinstance(entry, dict) or not isinstance(entry.get('object'), str): fail()
            value = entry.get('similarity')
            if type(value) not in (int, float) or not math.isfinite(value): fail()
    return objects
