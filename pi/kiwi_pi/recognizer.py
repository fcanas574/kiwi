"""Clientes del modelo de reconocimiento (Colab) y la decisión final de la estación."""
from __future__ import annotations

import asyncio
import random
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
            return self._get_client().predict(handle_file(tmp.name), api_name='/identify')

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
        await asyncio.sleep(0.6)
        categories = [s.category for s in self.cfg.slots] + ['capacitors', 'multimeter_box']
        top = self.force or random.choice(categories)
        scores = {c: round(random.uniform(0.30, 0.62), 4) for c in categories}
        scores[top] = round(random.uniform(0.82, 0.95), 4)
        ranking = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return {
            'candidate': top, 'recognized': True, 'similarity': ranking[0][1],
            'margin': ranking[0][1] - ranking[1][1], 'reason': 'Simulación (driver mock).',
            'mode': 'mock', 'ranking': [{'object': o, 'similarity': s} for o, s in ranking],
        }

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
