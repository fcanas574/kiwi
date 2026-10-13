"""Clientes del modelo de reconocimiento (Colab) y la decisión final de la estación."""
from __future__ import annotations

import asyncio
import random
import io
import math
import json
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
    capabilities: dict[str, bool] | None = None


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

    async def detect(self, jpeg: bytes, correlation: dict) -> dict:
        raise NotImplementedError

    async def classify(self, jpeg: bytes, correlation: dict) -> dict:
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
        return self._base_url() + '/health'

    def _base_url(self) -> str:
        url = self.url.rstrip('/')
        return url[:-len('/predict')] if url.endswith('/predict') else url

    def _operation_url(self, operation: str) -> str:
        return self._base_url() + f'/{operation}'

    def _configured(self) -> bool:
        return self.url.startswith(('http://', 'https://')) and 'TU-URL' not in self.url

    async def _post_json(self, url: str, jpeg: bytes, correlation: dict | None = None) -> dict:
        kwargs = {'headers': self._headers(),
                  'files': {'file': ('foto.jpg', jpeg, 'image/jpeg')}}
        if correlation is not None:
            try:
                kwargs['data'] = {'correlation': json.dumps(correlation)}
            except (TypeError, ValueError) as exc:
                raise RecognizerError('La correlación debe ser un objeto JSON.') from exc
        try:
            response = await self.client.post(url, **kwargs)
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

    async def predict(self, jpeg: bytes) -> dict:
        if not self._configured():
            raise RecognizerError('Configura la URL del endpoint de Colab.')
        return await self._post_json(self.url, jpeg)

    async def _scan(self, operation: str, jpeg: bytes, correlation: dict) -> dict:
        if not self._configured():
            raise RecognizerError('Configura la URL del endpoint de Colab.')
        if not isinstance(correlation, dict):
            raise RecognizerError('La correlación debe ser un objeto JSON.')
        return await self._post_json(self._operation_url(operation), jpeg, correlation)

    async def detect(self, jpeg: bytes, correlation: dict) -> dict:
        return await self._scan('detect', jpeg, correlation)

    async def classify(self, jpeg: bytes, correlation: dict) -> dict:
        return await self._scan('classify', jpeg, correlation)

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
        return Health(True, 'OK', data.get('mode'), data.get('calibrated'),
                      data.get('capabilities'))

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

    async def detect(self, jpeg: bytes, correlation: dict) -> dict:
        raise RecognizerError('El escaneo continuo solo está disponible con REST; Gradio admite el modo manual.')

    async def classify(self, jpeg: bytes, correlation: dict) -> dict:
        raise RecognizerError('El escaneo continuo solo está disponible con REST; Gradio admite el modo manual.')

    async def health(self) -> Health:
        if 'gradio' not in self.url:
            return Health(False, 'Sin URL de Gradio')
        try:
            await asyncio.to_thread(self._get_client)
            return Health(True, 'OK', capabilities={'predict': True, 'detect': False,
                                                    'classify': False})
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

    async def detect(self, jpeg: bytes, correlation: dict) -> dict:
        if not isinstance(correlation, dict):
            raise RecognizerError('La correlación debe ser un objeto JSON.')
        with Image.open(io.BytesIO(jpeg)) as image:
            width, height = image.size
        categories = self.force.split(',') if self.force else ['digital_multimeter', 'servo_motor', 'solder']
        objects = []
        for i, category in enumerate(categories[:12], 1):
            # Fixed twelve-cell layout: appending a target must not move the
            # existing detections and invalidate their geometric stability.
            column, row = (i - 1) % 4, (i - 1) // 4
            x1, y1 = int(column * width / 4), int(row * height / 3)
            x2 = min(width, max(x1 + 1, int((column + 1) * width / 4)))
            y2 = min(height, max(y1 + 1, int((row + 1) * height / 3)))
            objects.append({'id': i, 'box': [x1, y1, x2, y2],
                            'detector_label': category, 'detector_score': .5})
        return {'correlation': correlation,
                'result': {'image_size': [width, height], 'objects': objects,
                           'raw_detections': len(objects), 'detection_seconds': 0.0}}

    async def classify(self, jpeg: bytes, correlation: dict) -> dict:
        if not isinstance(correlation, dict):
            raise RecognizerError('La correlación debe ser un objeto JSON.')
        categories = self.force.split(',') if self.force else ['digital_multimeter', 'servo_motor', 'solder']
        try:
            object_id = int(correlation.get('object_id', correlation.get('id', 1)))
        except (TypeError, ValueError):
            object_id = 1
        category = categories[(max(1, object_id) - 1) % len(categories)]
        recognized = category != 'otros'
        result = {'candidate': category, 'recognized': recognized, 'similarity': .95,
                  'margin': .75, 'reason': 'Simulación (driver mock).', 'mode': 'mock',
                  'ranking': [{'object': category, 'similarity': .95}]}
        return {'correlation': correlation, 'result': result}

    async def health(self) -> Health:
        return Health(True, 'Simulación', 'mock', True,
                      {'predict': True, 'detect': True, 'classify': True})


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
            if not all(type(v) is int for v in box): fail()
            x1, y1, x2, y2 = box
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height): fail()
        x1, y1, x2, y2 = item['box']
        cx1, cy1, cx2, cy2 = item['crop_box']
        pad = max(2, round(min(x2 - x1, y2 - y1) * .04))
        if not (max(0, x1 - pad) <= cx1 <= x1 and max(0, y1 - pad) <= cy1 <= y1
                and x2 <= cx2 <= min(width, x2 + pad) and y2 <= cy2 <= min(height, y2 + pad)):
            fail()
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


def _split_result(payload: dict) -> dict:
    if isinstance(payload, dict) and isinstance(payload.get('result'), dict):
        return payload['result']
    return payload


def validate_detections(payload: dict, image_size: tuple[int, int]) -> list[dict]:
    """Validate DINO boxes before the caller uses them to crop the source frame."""
    def fail():
        raise RecognizerError('La API no devolvió detecciones válidas. Repite la captura.')

    payload = _split_result(payload)
    if not isinstance(payload, dict) or payload.get('image_size') != list(image_size):
        fail()
    objects = payload.get('objects')
    if not isinstance(objects, list) or len(objects) > 12:
        fail()
    width, height = image_size
    seen = set()
    for item in objects:
        if not isinstance(item, dict):
            fail()
        object_id = item.get('id')
        if type(object_id) is not int or object_id < 1 or object_id in seen:
            fail()
        seen.add(object_id)
        box = item.get('box')
        if not isinstance(box, list) or len(box) != 4 or not all(type(v) is int for v in box):
            fail()
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            fail()
        if not isinstance(item.get('detector_label'), str):
            fail()
        score = item.get('detector_score')
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            fail()
    raw_count = payload.get('raw_detections')
    elapsed = payload.get('detection_seconds')
    if type(raw_count) is not int or raw_count < len(objects):
        fail()
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        fail()
    return objects


def validate_classification(payload: dict) -> dict:
    """Reject inconsistent Qwen results before a scan may act on them."""
    def fail():
        raise RecognizerError('La API no devolvió una clasificación válida. Repite la captura.')

    result = _split_result(payload)
    if not isinstance(result, dict) or type(result.get('recognized')) is not bool:
        fail()
    candidate = result.get('candidate')
    if candidate is not None and (not isinstance(candidate, str) or not candidate):
        fail()
    recognized = result['recognized']
    if recognized and candidate is None:
        fail()
    if not isinstance(result.get('reason'), str):
        fail()
    ranking = result.get('ranking')
    if not isinstance(ranking, list):
        fail()
    for entry in ranking:
        if not isinstance(entry, dict) or not isinstance(entry.get('object'), str):
            fail()
        score = entry.get('similarity')
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            fail()
    similarity = result.get('similarity')
    margin = result.get('margin')
    for value in (similarity, margin):
        if value is not None and (type(value) not in (int, float)
                                  or not math.isfinite(value) or not 0 <= value <= 1):
            fail()
    if candidate is not None:
        if similarity is None or margin is None or not ranking or ranking[0]['object'] != candidate:
            fail()
    elif recognized or similarity is not None or margin is not None or ranking:
        fail()
    return result
