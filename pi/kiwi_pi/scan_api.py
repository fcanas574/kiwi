"""Paired phone endpoints for continuous scanning and crop corrections."""
from __future__ import annotations

import hmac
import secrets
import time
from collections import OrderedDict
from typing import Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .config import Config
from .scan import MAX_FRAME_BYTES, ScanCoordinator, ScanError

PAIR_COOKIE = 'kiwi_scan_pair'


class PairBody(BaseModel):
    code: str


class StartBody(BaseModel):
    session_id: str | None = None


class SessionBody(BaseModel):
    session_id: str


class CorrectionPrepareBody(SessionBody):
    track_id: int
    revision: int


class CorrectionSubmitBody(BaseModel):
    correction_id: str
    actual: str


class CorrectionCancelBody(BaseModel):
    correction_id: str


def register_scan_routes(app: FastAPI, get_scan: Callable[[], ScanCoordinator], cfg: Config) -> None:
    """Install scan routes and keep pairing identities private to this app instance."""
    paired_owners: OrderedDict[str, tuple[str, float]] = OrderedDict()
    correction_owners: dict[str, str] = {}

    def pause_owner(owner: str) -> None:
        scan = get_scan()
        session_id = scan.snapshot()['session_id']
        if session_id is not None and scan.owns(owner, session_id):
            scan.pause(session_id)

    def purge_expired_pairings() -> None:
        now = time.time()
        for token, (owner, expires_at) in list(paired_owners.items()):
            if expires_at <= now:
                paired_owners.pop(token, None)
                # Release expired capture authority without ending the return cycle.
                pause_owner(owner)

    def paired_owner(request: Request) -> str:
        purge_expired_pairings()
        token = request.cookies.get(PAIR_COOKIE)
        entry = paired_owners.get(token or '')
        if entry is None:
            raise HTTPException(401, 'Empareja este teléfono antes de controlar el escaneo.')
        owner, _ = entry
        return owner

    def require_owner(scan: ScanCoordinator, owner: str, session_id: str) -> None:
        if not scan.owns(owner, session_id):
            raise HTTPException(409, 'Este teléfono no controla la sesión de escaneo.')

    def owned_correction(request: Request, correction_id: str) -> str:
        owner = paired_owner(request)
        if correction_owners.get(correction_id) != owner:
            raise HTTPException(404, 'El recorte congelado no existe.')
        return owner

    def translate_scan_error(exc: ScanError) -> HTTPException:
        return HTTPException(exc.status_code, exc.detail)

    @app.post('/api/scan/pair')
    async def pair(body: PairBody, response: Response, request: Request):
        pairing_key = getattr(cfg, 'scan_pairing_key', '')
        if not pairing_key:
            raise HTTPException(503, 'Configura KIWI_SCAN_PAIRING_KEY en la estación.')
        if pairing_key == cfg.section('recognizer').get('api_key', ''):
            raise HTTPException(503, 'KIWI_SCAN_PAIRING_KEY debe ser distinto de KIWI_API_KEY.')
        if not hmac.compare_digest(body.code.encode('utf-8'), pairing_key.encode('utf-8')):
            raise HTTPException(401, 'Código de emparejamiento incorrecto.')
        purge_expired_pairings()
        previous = paired_owners.pop(request.cookies.get(PAIR_COOKIE, ''), None)
        token = secrets.token_urlsafe(32)
        owner = previous[0] if previous else secrets.token_hex(16)
        if previous:
            # Rotation retains this phone's correction ownership, but requires
            # an explicit start with a new generation before sending frames.
            pause_owner(owner)
        paired_owners[token] = (owner, time.time() + 43_200)
        while len(paired_owners) > 64:
            _, (evicted_owner, _) = paired_owners.popitem(last=False)
            pause_owner(evicted_owner)
        response.set_cookie(PAIR_COOKIE, token, max_age=43_200, httponly=True,
                            secure=True, samesite='strict', path='/api/scan')
        return {'paired': True}

    @app.get('/api/scan/state')
    async def scan_state():
        return get_scan().snapshot()

    @app.post('/api/scan/start')
    async def start(request: Request, body: StartBody | None = None):
        owner = paired_owner(request)
        try:
            return get_scan().start(owner, body.session_id if body else None)
        except ScanError as exc:
            raise translate_scan_error(exc) from exc

    @app.post('/api/scan/pause')
    async def pause(body: SessionBody, request: Request):
        scan = get_scan()
        owner = paired_owner(request)
        require_owner(scan, owner, body.session_id)
        try:
            return scan.pause(body.session_id)
        except ScanError as exc:
            raise translate_scan_error(exc) from exc

    @app.post('/api/scan/cancel')
    async def cancel(body: SessionBody, request: Request):
        scan = get_scan()
        owner = paired_owner(request)
        require_owner(scan, owner, body.session_id)
        try:
            return scan.cancel(body.session_id)
        except ScanError as exc:
            raise translate_scan_error(exc) from exc

    @app.post('/api/scan/frame')
    async def frame(request: Request, session_id: str = Form(...),
                    generation: int = Form(...), sequence: int = Form(...),
                    file: UploadFile = File(...)):
        scan = get_scan()
        owner = paired_owner(request)
        require_owner(scan, owner, session_id)
        if file.content_type not in ('image/jpeg', 'image/jpg', 'application/octet-stream'):
            raise HTTPException(400, 'El cuadro debe ser JPEG.')
        data = await file.read(MAX_FRAME_BYTES + 1)
        if len(data) > MAX_FRAME_BYTES:
            raise HTTPException(413, 'El cuadro supera el tamaño permitido.')
        try:
            return await scan.process_frame(session_id, generation, sequence, data)
        except ScanError as exc:
            raise translate_scan_error(exc) from exc

    @app.post('/api/scan/correction/prepare')
    async def correction_prepare(body: CorrectionPrepareBody, request: Request):
        scan = get_scan()
        owner = paired_owner(request)
        require_owner(scan, owner, body.session_id)
        try:
            result = scan.freeze_correction(body.session_id, body.track_id, body.revision)
        except ScanError as exc:
            raise translate_scan_error(exc) from exc
        correction_owners.clear()
        correction_owners[result['correction_id']] = owner
        return result

    @app.get('/api/scan/correction/{correction_id}/crop')
    async def correction_crop(correction_id: str, request: Request):
        owned_correction(request, correction_id)
        try:
            crop = get_scan().correction_crop(correction_id)
        except ScanError as exc:
            if exc.status_code in (404, 410):
                correction_owners.pop(correction_id, None)
            raise translate_scan_error(exc) from exc
        return Response(crop, media_type='image/jpeg',
                        headers={'Cache-Control': 'no-store'})

    @app.post('/api/scan/correction/submit')
    async def correction_submit(body: CorrectionSubmitBody, request: Request):
        owned_correction(request, body.correction_id)
        try:
            result = get_scan().submit_correction(body.correction_id, body.actual)
        except ScanError as exc:
            if exc.status_code in (404, 410):
                correction_owners.pop(body.correction_id, None)
            raise translate_scan_error(exc) from exc
        correction_owners.pop(body.correction_id, None)
        return result

    @app.post('/api/scan/correction/cancel')
    async def correction_cancel(body: CorrectionCancelBody, request: Request):
        owned_correction(request, body.correction_id)
        try:
            get_scan().cancel_correction(body.correction_id)
        except ScanError as exc:
            if exc.status_code in (404, 410):
                correction_owners.pop(body.correction_id, None)
            raise translate_scan_error(exc) from exc
        correction_owners.pop(body.correction_id, None)
        return {'ok': True}
