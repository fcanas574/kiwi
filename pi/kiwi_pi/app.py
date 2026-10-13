"""Servidor de la estación: interfaz táctil, cámara, reconocimiento, LEDs y sensores."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image

from .camera import Camera, make_camera, normalize_upload
from .config import Config, load_config
from .recognizer import RecognizerError, decide, make_recognizer, validate_objects
from .scan import ScanCoordinator
from .scan_api import register_scan_routes
from .station import Station

STATIC = Path(__file__).parent / 'static'
MAX_UPLOAD = 15 * 1024 * 1024
LOCAL_HOSTS = {'127.0.0.1', '::1', 'localhost'}
OTHER_CATEGORY = 'otros'   # referencias negativas: objetos que no deben encender un LED


class PhotoStore:
    """Últimas fotos en memoria; solo se guardan en disco si se pide (capturas o retroalimentación)."""

    def __init__(self, limit: int = 8):
        self.limit = limit
        self.items: OrderedDict[str, dict] = OrderedDict()

    def add(self, jpeg: bytes, source: str) -> str:
        photo_id = uuid.uuid4().hex[:12]
        self.items[photo_id] = {'jpeg': jpeg, 'source': source, 'result': None}
        while len(self.items) > self.limit:
            self.items.popitem(last=False)
        return photo_id

    def get(self, photo_id: str) -> dict:
        if photo_id not in self.items:
            raise HTTPException(404, 'La foto ya no está disponible. Toma otra.')
        return self.items[photo_id]


class PhotoRef(BaseModel):
    photo_id: str


class NextBody(BaseModel):
    photo_id: str | None = None


class RejectBody(PhotoRef):
    object_id: int | None = None
    actual: str | None = None   # categoría real, 'otros' (objeto ajeno) o None si no se indicó


class EndpointBody(BaseModel):
    url: str


class SimBody(BaseModel):
    occupied: bool


def create_app(cfg: Config | None = None, camera: Camera | None = None) -> FastAPI:
    cfg = cfg or load_config()
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cam = camera or make_camera(cfg)
        cam.start()
        recognizer = make_recognizer(cfg)
        station = Station(cfg, recognizer, camera_name=cam.name)
        scan = ScanCoordinator(station, recognizer, cfg, time.monotonic)
        await station.start()
        state.update(camera=cam, station=station, scan=scan, photos=PhotoStore())
        app.state.scan = scan
        yield
        await scan.close()
        await station.stop()
        cam.stop()

    app = FastAPI(title='Kiwi · estación Raspberry Pi', lifespan=lifespan)

    def station() -> Station:
        return state['station']

    def scan() -> ScanCoordinator:
        return state['scan']

    def live_snapshot() -> dict:
        return {**station().snapshot(), 'scan': scan().snapshot()}

    def require_no_continuous_scan() -> None:
        if scan().snapshot()['capture_state'] == 'running' or station().continuous:
            raise HTTPException(409, 'La estación está usando una guía de escaneo continuo.')

    register_scan_routes(app, lambda: state['scan'], cfg)

    def is_admin(request: Request) -> bool:
        host = request.client.host if request.client else ''
        return bool(cfg.section('server').get('allow_remote_admin')) or host in LOCAL_HOSTS

    def require_admin(request: Request) -> None:
        if not is_admin(request):
            raise HTTPException(403, 'Disponible solo desde la pantalla de la estación.')

    def save_photo(folder: str, name: str, jpeg: bytes) -> Path:
        path = cfg.data_dir / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jpeg)
        return path

    def log_recognition(result: dict) -> None:
        """Una fila por foto en data/recognitions.csv para calibrar los umbrales."""
        path = cfg.data_dir / 'recognitions.csv'
        ranking = result['ranking']
        second = ranking[1] if len(ranking) > 1 else {}
        row = [datetime.now().isoformat(timespec='seconds'), result['photo_id'], result['status'],
               result['category'] or '', result['similarity'] if result['similarity'] is not None else '',
               result['margin'] if result['margin'] is not None else '', second.get('object', ''),
               second.get('similarity', ''), result['reason']]
        new_file = not path.exists()
        with path.open('a', newline='') as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow(['fecha', 'photo_id', 'estado', 'candidato', 'similitud', 'margen',
                                 'segundo', 'similitud_segundo', 'motivo'])
            writer.writerow(row)
        print(f"Reconocimiento: {result['status']} · {result['category']} · "
              f"similitud {result['similarity']} · margen {result['margin']}")

    def store(jpeg: bytes, source: str) -> dict:
        photo_id = state['photos'].add(jpeg, source)
        if cfg.section('ui').get('save_captures'):
            save_photo('captures', f'{datetime.now():%Y%m%d-%H%M%S}-{photo_id}.jpg', jpeg)
        return {'photo_id': photo_id, 'url': f'/api/photos/{photo_id}.jpg', 'source': source}

    # ---------- estado ----------
    @app.get('/api/status')
    async def status(request: Request):
        ui = cfg.section('ui')
        return {**live_snapshot(), 'camera_available': state['camera'].available,
                'auto_send': bool(ui.get('auto_send')), 'test_mode': bool(cfg.section('decision').get('test_mode')),
                'admin': is_admin(request)}

    @app.get('/api/events')
    async def events(request: Request):
        queue = station().bus.subscribe()

        async def stream():
            try:
                yield f'data: {json.dumps(live_snapshot())}\n\n'
                while not await request.is_disconnected():
                    try:
                        await asyncio.wait_for(queue.get(), timeout=15)
                        yield f'data: {json.dumps(live_snapshot())}\n\n'
                    except asyncio.TimeoutError:
                        yield ': ping\n\n'
            finally:
                station().bus.unsubscribe(queue)

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # ---------- cámara y fotos ----------
    @app.get('/api/camera/stream')
    async def camera_stream(request: Request):
        cam = state['camera']
        if not cam.available:
            raise HTTPException(404, 'Sin cámara local.')

        async def frames():
            last = None
            while not await request.is_disconnected():
                frame = cam.latest_frame()
                if frame is not None and frame is not last:
                    last = frame
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
                await asyncio.sleep(0.05)

        return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=frame')

    @app.post('/api/capture')
    async def capture():
        cam = state['camera']
        if not cam.available:
            raise HTTPException(409, 'No hay cámara conectada a la Raspberry Pi.')
        try:
            jpeg = await asyncio.to_thread(cam.capture)
        except Exception as exc:
            raise HTTPException(500, f'No se pudo tomar la foto: {exc}') from exc
        return store(jpeg, 'camera')

    @app.post('/api/upload')
    async def upload(file: UploadFile = File(...), source: str = 'upload'):
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, 'La foto supera 15 MB.')
        try:
            jpeg = await asyncio.to_thread(normalize_upload, data)
        except Exception as exc:
            raise HTTPException(400, 'El archivo no es una imagen válida.') from exc
        return store(jpeg, 'camera' if source == 'browser-camera' else 'upload')

    @app.get('/api/photos/{photo_id}.jpg')
    async def photo(photo_id: str):
        return Response(state['photos'].get(photo_id)['jpeg'], media_type='image/jpeg',
                        headers={'Cache-Control': 'no-store'})

    # ---------- reconocimiento y guía ----------
    @app.post('/api/recognize')
    async def recognize(body: PhotoRef):
        item = state['photos'].get(body.photo_id)
        st = station()
        require_no_continuous_scan()
        scan_generation = scan().snapshot()['generation']
        state['active_photo_id'] = body.photo_id
        st.cancel_guide()   # una foto nueva apaga la señal anterior
        version = st.recognition_version
        started = time.perf_counter()
        try:
            api = await st.recognizer.predict(item['jpeg'])
        except RecognizerError as exc:
            await st.refresh_health()
            return {'status': 'error', 'reason': str(exc)}
        require_no_continuous_scan()
        if scan_generation != scan().snapshot()['generation']:
            raise HTTPException(409, 'El escaneo cambió mientras se reconocía la foto.')
        if version != st.recognition_version:
            return {'status': 'error', 'reason': 'Reconocimiento cancelado o reemplazado por otra foto.'}
        try:
            with Image.open(io.BytesIO(item['jpeg'])) as image:
                image_size = image.size
            raw_objects = validate_objects(api, image_size)
        except RecognizerError as exc:
            return {'status': 'error', 'reason': str(exc)}
        operation_id = uuid.uuid4().hex
        labels = {s.category: s.label for s in cfg.slots} | {OTHER_CATEGORY: 'Ninguna herramienta'}
        objects, slots = [], {}
        for raw in raw_objects:
            recognition = raw['recognition']
            decision = decide(cfg, recognition)
            if not raw['recognized'] and not cfg.section('decision').get('test_mode'):
                decision.accepted = False
            slot = cfg.slot_for(decision.category)
            result = {**raw, 'photo_id': body.photo_id, 'operation_id': operation_id,
                      'status': 'accepted' if decision.accepted else 'rejected',
                      'category': decision.category, 'label': labels.get(decision.category, decision.category),
                      'similarity': decision.similarity, 'margin': decision.margin, 'reason': decision.reason,
                      'test_mode': decision.test_mode, 'mode': decision.mode,
                      'slot': slot.as_dict() if slot and decision.accepted else None,
                      'ranking': [{**r, 'label': labels.get(r['object'], r['object'])} for r in decision.ranking],
                      'elapsed_seconds': recognition.get('elapsed_seconds', 0)}
            objects.append(result)
            log_recognition(result)
            if slot and decision.accepted:
                slots[slot.id] = slot
        result = {'status': 'accepted' if slots else 'rejected', 'photo_id': body.photo_id,
                  'operation_id': operation_id, 'image_size': list(image_size), 'objects': objects,
                  'slots': [slot.as_dict() for slot in sorted(slots.values(), key=lambda s: s.id)],
                  'test_mode': any(o['test_mode'] for o in objects), 'mode': 'grounding_dino_qwen',
                  'reason': 'Devuelve las herramientas en las ubicaciones iluminadas.' if slots else
                            'No hay herramientas aceptadas para esta estación. Repite la foto.',
                  'elapsed_seconds': round(time.perf_counter() - started, 3)}
        item['result'] = result
        if slots:
            st.begin_guides(list(slots.values()), operation_id, result['test_mode'])
        return result

    @app.post('/api/next')
    async def next_tool(body: NextBody | None = None):
        if body and body.photo_id and state.get('active_photo_id') != body.photo_id:
            raise HTTPException(409, 'La estación está procesando otra foto.')
        require_no_continuous_scan()
        station().cancel_guide()
        return {'ok': True}

    @app.post('/api/reject')
    async def reject(body: RejectBody):
        require_no_continuous_scan()
        valid = {s.category for s in cfg.slots} | {OTHER_CATEGORY}
        if body.actual is not None and body.actual not in valid:
            raise HTTPException(400, 'Etiqueta desconocida.')
        item = state['photos'].get(body.photo_id)
        result = item['result']
        if state.get('active_photo_id') != body.photo_id:
            raise HTTPException(409, 'La estación está procesando otra foto.')
        if result is None:  # cancel a pending prediction, without adding dataset entries
            station().cancel_guide()
            return {'ok': True}
        selected = next((o for o in result['objects'] if o['id'] == body.object_id), None)
        if selected is None:
            raise HTTPException(404, 'Selecciona un objeto disponible de esta foto.')
        current_operation = station().operation_id
        if current_operation is not None and current_operation != result['operation_id']:
            raise HTTPException(409, 'La estación está procesando otra foto.')
        station().cancel_guide()
        if body.actual is None:
            return {'ok': True}
        with Image.open(io.BytesIO(item['jpeg'])) as image:
            crop = image.crop(selected['crop_box']).convert('RGB')
            buffer = io.BytesIO()
            crop.save(buffer, 'JPEG', quality=92)
        folder = f'feedback/{body.actual}'
        name = f'{datetime.now():%Y%m%d-%H%M%S}-{body.photo_id}-{body.object_id}-{uuid.uuid4().hex[:6]}'
        save_photo(folder, f'{name}.jpg', buffer.getvalue())
        save_photo(folder, f'{name}.json', json.dumps({**selected, 'actual': body.actual},
                                                      ensure_ascii=False, indent=2).encode())
        return {'ok': True}

    # ---------- administración (pantalla de la estación) ----------
    @app.put('/api/endpoint')
    async def set_endpoint(body: EndpointBody, request: Request):
        require_admin(request)
        url = body.url.strip()
        if not url.startswith(('http://', 'https://')) or len(url) > 300:
            raise HTTPException(400, 'Usa una URL que empiece por https://')
        station().recognizer.set_url(url)
        cfg.save_setting('recognizer_url', url)
        await station().refresh_health()
        station().notify()
        return {'url': url}

    @app.post('/api/leds/test')
    async def leds_test(request: Request):
        require_admin(request)
        asyncio.create_task(station().led_self_test())
        return {'ok': True}

    @app.post('/api/leds/off')
    async def leds_off(request: Request):
        require_admin(request)
        scan().cancel_for_manual()
        return {'ok': True}

    @app.post('/api/sim/slots/{slot_id}')
    async def simulate_slot(slot_id: int, body: SimBody, request: Request):
        require_admin(request)
        if not cfg.slot_by_id(slot_id):
            raise HTTPException(404, 'Posición desconocida.')
        try:
            station().simulate_sensor(slot_id, body.occupied)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'ok': True}

    @app.post('/api/sim/recognizer')
    async def simulate_recognizer(request: Request, category: str | None = None):
        require_admin(request)
        recognizer = station().recognizer
        if recognizer.name != 'mock':
            raise HTTPException(409, 'Solo con recognizer.driver: mock')
        recognizer.force = category or None
        return {'force': recognizer.force}

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-cache'})

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app
