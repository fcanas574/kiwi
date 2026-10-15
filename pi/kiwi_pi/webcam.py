"""Phone supplies frames; only the local touchscreen controls recognition."""
from __future__ import annotations

import asyncio
import io
import time
import uuid

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel

from .scan import ScanError, _normalize_scan_jpeg

MAX_WEBCAM_BYTES = 2 * 1024 * 1024


class ModeBody(BaseModel):
    mode: str


class WebcamBridge:
    """Keep one latest frame and one analyzed frame, never an inference backlog."""

    def __init__(self, scan, clock=time.monotonic):
        self.scan = scan
        self.clock = clock
        self.owner = None
        self.station_owner = 'station:' + uuid.uuid4().hex
        self.jpeg = None
        self.size = None
        self.received_at = None
        self.revision = 0
        self.analyzed = None
        self.analysis_revision = 0
        self.analysis_tracks = []
        self.analysis_size = None
        self.error = None
        self._ingest_lock = asyncio.Lock()
        self._task = None
        self._monitor_task = None
        self.first_confirmation_seconds = None
        self._started_at = None
        self._initial_targets = set()

    def online(self):
        return self.received_at is not None and self.clock() - self.received_at < 4

    def snapshot(self):
        return {'online': self.online(), 'revision': self.revision,
                'analysis_revision': self.analysis_revision,
                'analysis_tracks': self.analysis_tracks, 'analysis_size': self.analysis_size,
                'first_confirmation_seconds': self.first_confirmation_seconds,
                'error': self.error}

    async def ingest(self, owner, data):
        if len(data) > MAX_WEBCAM_BYTES:
            raise ScanError(413, 'La imagen de cámara supera 2 MB.')
        if self.owner != owner and self.online():
            raise ScanError(409, 'Otra cámara ya está conectada.')
        if self._ingest_lock.locked():
            raise ScanError(429, 'Envía solo una imagen a la vez.')
        async with self._ingest_lock:
            def normalize():
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        if image.format != 'JPEG' or image.width * image.height > 4_000_000:
                            raise ValueError('JPEG demasiado grande')
                except Exception as exc:
                    raise ScanError(400, 'Usa una imagen JPEG de cámara de hasta 4 megapíxeles.') from exc
                return _normalize_scan_jpeg(data, 960)
            jpeg, size = await asyncio.to_thread(normalize)
            self.owner = owner
            self.jpeg, self.size = jpeg, size
            self.received_at = self.clock()
            self.revision += 1
            return {'ok': True, 'revision': self.revision}

    def start(self):
        if not self.online():
            raise ScanError(409, 'Abre la cámara en el iPhone antes de iniciar.')
        was_running = self.scan.snapshot()['capture_state'] == 'running'
        result = self.scan.start(self.station_owner)
        if not was_running:
            self._started_at = self.clock()
            self.first_confirmation_seconds = None
            self._initial_targets = {slot['id'] for slot in result['guide']['targets']}
        self.error = None
        return result

    def pause(self):
        state = self.scan.snapshot()
        return self.scan.pause(state['session_id']) if state['session_id'] else state

    def finish(self):
        result = self.scan.finish_station_cycle()
        self.analyzed = None
        self.analysis_tracks = []
        self.analysis_size = None
        self.error = None
        self.analysis_revision += 1
        self.first_confirmation_seconds = None
        return result

    def run(self):
        self._task = asyncio.create_task(self._pump())
        self._monitor_task = asyncio.create_task(self._monitor())

    async def close(self):
        tasks = [task for task in (self._task, self._monitor_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _monitor(self):
        while True:
            await asyncio.sleep(0.5)
            state = self.scan.snapshot()
            if (state['capture_state'] == 'running' and not self.online()
                    and self.scan.owns(self.station_owner, state['session_id'])):
                self.pause()
                self.error = 'Cámara desconectada. Reconecta el iPhone e inicia desde la estación.'
                self.scan.station.notify()

    async def _pump(self):
        consumed = 0
        next_at = 0
        interval = 1 / max(0.1, min(1, float(self.scan.cfg.section('scan').get('max_fps', 1))))
        while True:
            await asyncio.sleep(0.05)
            state = self.scan.snapshot()
            if (state['capture_state'] != 'running' or not self.online()
                    or consumed == self.revision or state['busy']
                    or self.clock() < next_at
                    or state['retry_after_seconds'] > 0
                    or not self.scan.owns(self.station_owner, state['session_id'])):
                continue
            revision, jpeg, size = self.revision, self.jpeg, self.size
            # Cap request start frequency, rather than adding a full delay
            # after the detector has already spent time analyzing the frame.
            next_at = self.clock() + interval
            try:
                result = await self.scan.process_frame(
                    state['session_id'], state['generation'], state['last_sequence'] + 1, jpeg)
                consumed = revision
                current = self.scan.snapshot()
                if (current['generation'] == state['generation']
                        and current['capture_state'] == 'running' and not result.get('discarded')):
                    self.analyzed, self.analysis_size = jpeg, list(size)
                    self.analysis_tracks = result.get('tracks', [])
                    self.analysis_revision += 1
                    targets = {slot['id'] for slot in result['guide']['targets']}
                    if (self.first_confirmation_seconds is None and self._started_at is not None
                            and targets - self._initial_targets):
                        self.first_confirmation_seconds = round(self.clock() - self._started_at, 1)
                    self.error = result.get('error')
                    self.scan.station.notify()
            except ScanError as exc:
                self.error = exc.detail
                self.scan.station.notify()
            except Exception:
                self.error = 'No se pudo analizar la imagen. Reintenta desde la estación.'
                self.scan.station.notify()


def register_webcam_routes(app, get_bridge, paired_owner, require_admin):
    @app.put('/api/station/scan/mode')
    async def scan_mode(body: ModeBody, request: Request):
        require_admin(request)
        if body.mode not in ('single', 'tracked'):
            raise HTTPException(400, 'Selecciona un objeto o varios objetos.')
        bridge = get_bridge()
        bridge.finish()
        bridge.scan.cfg.raw.setdefault('scan', {})['mode'] = body.mode
        bridge.scan.cfg.save_setting('scan_mode', body.mode)
        bridge.scan.station.notify()
        return bridge.scan.snapshot()
    @app.get('/api/scan/webcam/state')
    async def webcam_state(request: Request):
        paired_owner(request)
        return {'paired': True, 'online': get_bridge().online()}

    @app.post('/api/scan/webcam/frame')
    async def webcam_frame(request: Request, file: UploadFile = File(...)):
        owner = paired_owner(request)
        data = await file.read(MAX_WEBCAM_BYTES + 1)
        try:
            return await get_bridge().ingest(owner, data)
        except ScanError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

    @app.post('/api/station/scan/{action}')
    async def station_control(action: str, request: Request):
        require_admin(request)
        bridge = get_bridge()
        methods = {'start': bridge.start, 'pause': bridge.pause, 'finish': bridge.finish}
        if action not in methods:
            raise HTTPException(404, 'Acción desconocida.')
        try:
            return methods[action]()
        except ScanError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

    @app.get('/api/station/webcam/{kind}.jpg')
    async def webcam_image(kind: str, request: Request, v: int | None = None):
        require_admin(request)
        bridge = get_bridge()
        if kind == 'analysis' and v is not None and v != bridge.analysis_revision:
            raise HTTPException(409, 'El análisis cambió. Actualiza la imagen.')
        data = bridge.jpeg if kind == 'live' else bridge.analyzed if kind == 'analysis' else None
        if data is None:
            raise HTTPException(404, 'Esperando una imagen de cámara.')
        return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})
