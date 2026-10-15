"""Coordinate one phone's continuous scan without blocking station events."""
from __future__ import annotations

import asyncio
import io
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from PIL import Image, ImageOps

from .config import Config, Slot
from .recognizer import (MockRecognizer, Recognizer, RecognizerError, decide,
                         validate_classification, validate_detections)
from .station import Station
from .tracking import Tracker

MAX_FRAME_BYTES = 15 * 1024 * 1024
MAX_TRACKS = 12
CORRECTION_TTL_SECONDS = 300
_BACKOFF_SECONDS = (1, 2, 4, 8, 15)


class ScanError(RuntimeError):
    """A scan request that must be returned to the client with an HTTP status."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class _FrozenCorrection:
    correction_id: str
    crop: bytes
    metadata: dict
    expires_at_clock: float
    owner: str | None
    generation: int
    operation_id: str | None
    operation_revision: int


class _StaleGeneration(Exception):
    pass


class ScanCoordinator:
    """Single-session async coordinator for DINO, Qwen and continuous guides."""

    def __init__(self, station: Station, recognizer: Recognizer, cfg: Config,
                 clock: Callable[[], float]):
        self.station = station
        self.recognizer = recognizer
        self.cfg = cfg
        self.clock = clock
        self.tracker = Tracker(cfg.raw)

        self._session_id: str | None = None
        self._owner: str | None = None
        self._generation = 0
        self._capture_state = 'paused'
        self._last_sequence = 0
        self._busy = False
        self._connection_error: str | None = None
        self._retry_at: float | None = None
        self._backoff_index = 0
        self._detection_seconds = 0.0
        self._classification_seconds = 0.0
        self._single_candidate = None
        self._single_count = 0
        self._single_track = None

        self._suppressed: set[str] = set()
        self._absence_counts: dict[str, int] = {}
        self._cycle_categories: set[str] = set()
        self._cycle_operation_id: str | None = None

        # Keep only the most recent valid DINO frame's crops (at most twelve).
        self._frame_jpeg: bytes | None = None
        self._frame_sequence: int | None = None
        self._frame_image_size: tuple[int, int] | None = None
        self._current_crops: dict[int, dict] = {}
        self._corrections: dict[str, _FrozenCorrection] = {}
        self._expired_corrections: deque[str] = deque(maxlen=32)
        self._correction_expiry_task: asyncio.Task | None = None

    def start(self, owner: str, session_id: str | None = None) -> dict:
        if not isinstance(owner, str) or not owner.strip():
            raise ScanError(400, 'Falta la identidad del dispositivo emparejado.')
        if session_id is not None and (not isinstance(session_id, str) or not session_id.strip()
                                       or len(session_id) > 128):
            raise ScanError(400, 'La sesión no es válida.')

        if self._capture_state == 'running':
            if self._owner != owner:
                raise ScanError(409, 'Ya hay otro dispositivo controlando el escaneo.')
            if session_id is not None and session_id != self._session_id:
                raise ScanError(409, 'La sesión indicada no coincide con la sesión activa.')
            return self.snapshot()

        if self.station.operation_id is not None and not self.station.continuous:
            raise ScanError(409, 'Termina la guía manual antes de iniciar el escaneo continuo.')

        if session_id is not None:
            self._session_id = session_id
        elif self._session_id is None:
            self._session_id = uuid.uuid4().hex
        self._owner = owner
        self._generation += 1
        self._capture_state = 'running'
        self._last_sequence = 0
        self._detection_seconds = 0.0
        self._classification_seconds = 0.0
        self.tracker.reset()
        self._discard_current_frame()
        self._sync_cycle()
        self.station.notify()
        return self.snapshot()

    def pause(self, session_id: str) -> dict:
        self._require_session(session_id)
        self._generation += 1
        self._capture_state = 'paused'
        # A different paired phone may take over only with a later explicit
        # start request while paused; the current phone can still cancel/resume.
        self.tracker.reset()
        self._discard_current_frame()
        self.station.notify()
        return self.snapshot()

    def cancel(self, session_id: str) -> dict:
        self._require_session(session_id)
        self._generation += 1
        self._capture_state = 'paused'
        self._owner = None
        self.tracker.reset()
        self._discard_current_frame()
        self._cycle_categories.clear()
        self._cycle_operation_id = None
        self.station.cancel_guide()
        return self.snapshot()

    def cancel_for_manual(self) -> None:
        """Invalidate pending scan work before an explicit station-side reset."""
        if self._session_id is not None:
            self.cancel(self._session_id)
        else:
            self._generation += 1
            self._capture_state = 'paused'
            self._owner = None
            self.tracker.reset()
            self._discard_current_frame()
            self._cycle_categories.clear()
            self._cycle_operation_id = None
            self.station.cancel_guide()

    def owns(self, owner: str, session_id: str) -> bool:
        return self._owner == owner and self._session_id == session_id

    def finish_station_cycle(self) -> dict:
        """An explicit touchscreen finish turns lights off and rearms all objects."""
        self.cancel_for_manual()
        self._suppressed.clear()
        self._absence_counts.clear()
        self._connection_error = None
        self._retry_at = None
        self._backoff_index = 0
        self.station.notify()
        return self.snapshot()

    def snapshot(self) -> dict:
        self._purge_expired_corrections()
        retry_after = max(0.0, (self._retry_at or 0.0) - self.clock())
        return {
            'session_id': self._session_id,
            'generation': self._generation,
            'capture_state': self._capture_state,
            'last_sequence': self._last_sequence,
            'busy': self._busy,
            'mode': self.cfg.section('scan').get('mode', 'tracked'),
            'tracks': [dict(self._single_track)] if self._single_track else self.tracker.snapshot(),
            'connection_error': self._connection_error,
            'retry_after_seconds': round(retry_after, 3),
            'detection_seconds': self._detection_seconds,
            'classification_seconds': self._classification_seconds,
            'suppressed_categories': sorted(self._suppressed),
            'guide': self.station.snapshot()['guide'],
        }

    async def process_frame(self, session_id: str, generation: int, sequence: int,
                            jpeg: bytes) -> dict:
        self._validate_frame_request(session_id, generation, sequence)
        if self._busy:
            raise ScanError(409, 'El servidor ya procesa un cuadro.')
        retry_after = max(0.0, (self._retry_at or 0.0) - self.clock())
        if retry_after > 0:
            raise ScanError(409, f'El servicio remoto está en espera; reintenta en {retry_after:.1f} s.')
        if not isinstance(jpeg, bytes) or len(jpeg) > MAX_FRAME_BYTES:
            raise ScanError(413, 'El cuadro supera el tamaño permitido.')
        if type(sequence) is not int or sequence <= self._last_sequence:
            raise ScanError(409, 'El número del cuadro debe avanzar.')

        self._busy = True
        self._last_sequence = sequence
        self.station.notify()
        frame_generation = generation
        response_data: dict = {'detections': [], 'confirmed': []}
        request_correlation = {'session_id': session_id, 'generation': generation,
                               'sequence': sequence}
        try:
            max_side = min(960, max(1, int(self.cfg.section('scan').get('max_side', 960))))
            normalized_jpeg, image_size = await asyncio.to_thread(
                _normalize_scan_jpeg, jpeg, max_side)
            self._ensure_current(session_id, frame_generation)

            if self.cfg.section('scan').get('mode', 'tracked') == 'single':
                response_data = await self._process_single(
                    normalized_jpeg, image_size, session_id, frame_generation, sequence,
                    request_correlation)
            else:
                detection_started = time.perf_counter()
                detection_payload = await self.recognizer.detect(normalized_jpeg, request_correlation)
                self._ensure_current(session_id, frame_generation)
                self._detection_seconds = round(time.perf_counter() - detection_started, 3)
                self._require_correlation(detection_payload, request_correlation)
                detections = validate_detections(detection_payload, image_size)
                detections = _consolidate_detections(detections, self.cfg.section('scan'))

                frame_crops = await asyncio.to_thread(
                    _make_detection_crops, normalized_jpeg, image_size, detections)
                self._ensure_current(session_id, frame_generation)

                confirmed = self.tracker.update(sequence, image_size, detections, self.clock())
                self._sync_cycle()
                self._apply_confirmed(confirmed)
                self._frame_jpeg = normalized_jpeg
                self._frame_sequence = sequence
                self._frame_image_size = image_size
                self._current_crops = {
                    track['track_id']: {
                        **frame_crops[tuple(track['box'])],
                        'revision': track['revision'],
                        'sequence': sequence,
                        'box': list(track['box']),
                        'state': track['state'],
                        'category': track['category'],
                        'recognition': track['recognition'],
                    }
                    for track in self.tracker.snapshot()
                    if track['frame_id'] == sequence and tuple(track['box']) in frame_crops
                }

                candidates = []
                batch = getattr(self.recognizer, 'supports_batch', False)
                for _ in range(6 if batch else 1):
                    candidate = self.tracker.next_candidate(self.clock())
                    if candidate is None:
                        break
                    crop_item = self._current_crops.get(candidate['track_id'])
                    if crop_item is None or crop_item['revision'] != candidate['revision']:
                        self.tracker.classification_failed(candidate['track_id'], candidate['revision'],
                                                           self.clock())
                        continue
                    metadata = {**request_correlation, 'track_id': candidate['track_id'],
                                'revision': candidate['revision']}
                    if isinstance(self.recognizer, MockRecognizer):
                        detection = next(item for item in detections if item['box'] == candidate['box'])
                        metadata['object_id'] = detection['id']
                    candidates.append((candidate, crop_item['jpeg'], metadata))
                if candidates:
                    classification_started = time.perf_counter()
                    self.station.notify()
                    try:
                        if batch:
                            payloads = []
                            for offset in range(0, len(candidates), 3):
                                chunk = candidates[offset:offset + 3]
                                payloads.extend(await self.recognizer.classify_batch(
                                    [(crop, metadata) for _, crop, metadata in chunk]))
                                self._ensure_current(session_id, frame_generation)
                        else:
                            _, crop, metadata = candidates[0]
                            payloads = [await self.recognizer.classify(crop, metadata)]
                        self._ensure_current(session_id, frame_generation)
                        if not isinstance(payloads, list) or len(payloads) != len(candidates):
                            raise RecognizerError('Cantidad de clasificaciones inconsistente.')
                        # Validate the complete batch before accepting any result.
                        recognitions = []
                        for payload, (_, _, metadata) in zip(payloads, candidates):
                            self._require_correlation(payload, metadata)
                            recognitions.append(validate_classification(payload))
                        self._classification_seconds = round(time.perf_counter() - classification_started, 3)
                    except _StaleGeneration:
                        raise
                    except Exception:
                        if self._is_current(session_id, frame_generation):
                            for candidate, _, _ in candidates:
                                self.tracker.classification_failed(candidate['track_id'], candidate['revision'],
                                                                   self.clock())
                        raise
                    for (candidate, _, _), recognition in zip(candidates, recognitions):
                        self.tracker.set_provisional(candidate['track_id'], candidate['revision'],
                                                     recognition, self.clock())

                self._update_rearming()
                self._sync_cycle()
                self._clear_backoff()
                response_data = {'detections': detections, 'confirmed': confirmed}
        except _StaleGeneration:
            response_data = {'detections': [], 'confirmed': [], 'discarded': True}
        except ScanError:
            raise
        except Exception as exc:
            if self._is_current(session_id, frame_generation):
                self._set_connection_error(exc)
                response_data = {'detections': [], 'confirmed': [], 'error': self._connection_error}
            else:
                response_data = {'detections': [], 'confirmed': [], 'discarded': True}
        finally:
            self._busy = False
            self.station.notify()

        return {**self.snapshot(), **response_data}

    async def _process_single(self, jpeg, image_size, session_id, generation, sequence, correlation):
        """Two matching full-scene classifications for one presented instrument.

        This mode avoids the detector's part/background proposals. The operator
        presents one instrument at a time; tracked mode handles multiple crops.
        """
        self._single_track = {'track_id': 1, 'revision': generation,
                              'box': [0, 0, *image_size], 'frame_id': sequence,
                              'state': 'classifying', 'stable_samples': self._single_count,
                              'missing_samples': 0, 'category': None, 'recognition': None}
        self.station.notify()
        began = time.perf_counter()
        payload = await self.recognizer.classify(jpeg, correlation)
        self._ensure_current(session_id, generation)
        self._require_correlation(payload, correlation)
        recognition = validate_classification(payload)
        self._classification_seconds = round(time.perf_counter() - began, 3)
        decision = decide(self.cfg, recognition)
        accepted = decision.accepted and recognition.get('recognized') is not False
        category = decision.category if accepted else None
        if category and category == self._single_candidate:
            self._single_count += 1
        else:
            self._single_candidate = category
            self._single_count = 1 if category else 0
        confirmed = []
        self._sync_cycle()
        if category and self._single_count >= 2:
            confirmed = [{'track_id': 1, 'revision': generation, 'recognition': recognition}]
            self._apply_confirmed(confirmed)
        state = 'accepted' if confirmed else 'provisional' if category else 'rejected'
        self._single_track.update(state=state, stable_samples=min(2, self._single_count),
                                  category=category if confirmed else None,
                                  recognition=recognition)
        # In single-object mode, three valid classifications of another scene
        # permit rearming. Network failures and pauses never count as absence.
        for old in list(self._suppressed):
            if old == category:
                self._absence_counts[old] = 0
            else:
                self._absence_counts[old] = self._absence_counts.get(old, 0) + 1
                if self._absence_counts[old] >= 3:
                    self._suppressed.discard(old)
                    self._absence_counts.pop(old, None)
        self._frame_jpeg, self._frame_sequence = jpeg, sequence
        self._frame_image_size = image_size
        self._clear_backoff()
        return {'detections': [], 'confirmed': confirmed}

    def freeze_correction(self, session_id: str, track_id: int, revision: int) -> dict:
        self._require_session(session_id)
        if self._capture_state != 'running':
            raise ScanError(409, 'El escaneo debe estar activo para preparar una corrección.')
        self._purge_expired_corrections()
        if self._corrections:
            raise ScanError(409, 'Ya hay una corrección congelada pendiente.')
        item = self._current_crops.get(track_id)
        if (item is None or item['revision'] != revision
                or item['sequence'] != self._frame_sequence):
            raise ScanError(409, 'El recorte ya no corresponde al seguimiento actual.')

        correction_id = uuid.uuid4().hex
        expires_at_clock = self.clock() + CORRECTION_TTL_SECONDS
        expires_at = time.time() + CORRECTION_TTL_SECONDS
        frozen = _FrozenCorrection(
            correction_id=correction_id,
            crop=bytes(item['jpeg']),
            metadata={
                'session_id': session_id,
                'generation': self._generation,
                'sequence': item['sequence'],
                'track_id': track_id,
                'revision': revision,
                'box': list(item['box']),
                'image_size': list(self._frame_image_size or ()),
                'state': item.get('state'),
                'category': item.get('category'),
                'recognition': item.get('recognition'),
            },
            expires_at_clock=expires_at_clock,
            owner=self._owner,
            generation=self._generation + 1,
            operation_id=self.station.operation_id,
            operation_revision=self.station.operation_revision,
        )
        # Validate first, then pause and advance generation as one synchronous
        # event-loop operation. The crop remains independent of later frames.
        self._generation += 1
        self._capture_state = 'paused'
        self._corrections[correction_id] = frozen
        self.tracker.reset()
        self._discard_current_frame()
        self.station.notify()
        self._schedule_correction_expiry(correction_id)
        return {'correction_id': correction_id, 'generation': self._generation,
                'expires_at': expires_at}

    def correction_crop(self, correction_id: str) -> bytes:
        correction = self._get_correction(correction_id)
        return bytes(correction.crop)

    def submit_correction(self, correction_id: str, actual: str) -> dict:
        correction = self._get_correction(correction_id)
        if (correction.metadata['session_id'] != self._session_id
                or correction.generation != self._generation
                or correction.owner != self._owner
                or correction.operation_revision != self.station.operation_revision
                or self.station.operation_id not in (correction.operation_id, None)
                or self._capture_state != 'paused'):
            raise ScanError(409, 'La corrección ya no controla la sesión actual.')
        valid = {slot.category for slot in self.cfg.slots} | {'otros'}
        if not isinstance(actual, str) or actual not in valid:
            raise ScanError(400, 'Etiqueta desconocida.')

        folder = self.cfg.data_dir / 'feedback' / actual
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{datetime.now():%Y%m%d-%H%M%S}-scan-{correction_id[:12]}-{correction.metadata['track_id']}"
        image_path = folder / f'{stem}.jpg'
        metadata_path = folder / f'{stem}.json'
        metadata = {**correction.metadata, 'actual': actual}
        image_path.write_bytes(correction.crop)
        try:
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        except Exception:
            image_path.unlink(missing_ok=True)
            raise

        self._remove_correction(correction_id)
        self._capture_state = 'paused'
        self._owner = None
        self._generation += 1
        self._cycle_categories.clear()
        self._cycle_operation_id = None
        self.tracker.reset()
        self._discard_current_frame()
        self.station.cancel_guide()
        return {'ok': True}

    def cancel_correction(self, correction_id: str) -> None:
        correction = self._get_correction(correction_id)
        self._remove_correction(correction.correction_id)

    async def close(self) -> None:
        self._generation += 1
        self._capture_state = 'paused'
        self._owner = None
        if self._correction_expiry_task:
            self._correction_expiry_task.cancel()
            self._correction_expiry_task = None
        self._corrections.clear()
        self._discard_current_frame()

    def _validate_frame_request(self, session_id: str, generation: int, sequence: int) -> None:
        self._require_session(session_id)
        if self._capture_state != 'running':
            raise ScanError(409, 'La sesión de escaneo está pausada.')
        if type(generation) is not int or generation != self._generation:
            raise ScanError(409, 'La generación de escaneo ya cambió.')
        if type(sequence) is not int or sequence <= self._last_sequence:
            raise ScanError(409, 'El número del cuadro debe avanzar.')

    def _require_session(self, session_id: str) -> None:
        if not isinstance(session_id, str) or session_id != self._session_id:
            raise ScanError(409, 'La sesión de escaneo no coincide.')

    def _ensure_current(self, session_id: str, generation: int) -> None:
        if not self._is_current(session_id, generation):
            raise _StaleGeneration

    def _is_current(self, session_id: str, generation: int) -> bool:
        return (self._session_id == session_id and self._generation == generation
                and self._capture_state == 'running')

    @staticmethod
    def _require_correlation(payload: dict, expected: dict) -> None:
        if not isinstance(payload, dict) or payload.get('correlation') != expected:
            raise RecognizerError('La respuesta del modelo pertenece a otra solicitud.')

    def _apply_confirmed(self, events: list[dict]) -> None:
        additions: dict[int, Slot] = {}
        test_mode = bool(self.cfg.section('decision').get('test_mode'))
        for event in events:
            recognition = event['recognition']
            decision = decide(self.cfg, recognition)
            # Explicit Qwen rejection remains a rejection even if local similarity
            # thresholds would otherwise accept it. Test mode is the sole override.
            if recognition.get('recognized') is False and not test_mode:
                decision.accepted = False
            slot = self.cfg.slot_for(decision.category) if decision.accepted else None
            if slot is None or slot.category in self._suppressed:
                continue
            if slot.category in self._cycle_categories:
                continue
            self._suppressed.add(slot.category)
            self._absence_counts[slot.category] = 0
            self._cycle_categories.add(slot.category)
            additions[slot.id] = slot

        if not additions:
            return
        if self.station.continuous and self.station.operation_id:
            operation_id = self.station.operation_id
        else:
            operation_id = uuid.uuid4().hex
        self.station.add_guides(list(additions.values()), operation_id,
                                bool(self.cfg.section('decision').get('test_mode')))
        self._cycle_operation_id = operation_id

    def _update_rearming(self) -> None:
        if not self._suppressed:
            return
        visibility = self.tracker.category_visibility()
        tracks = self.tracker.snapshot()
        clear_observation = all(track['state'] in ('accepted', 'rejected') for track in tracks)
        needed = max(1, int(self.cfg.section('scan').get('rearm_samples', 3)))
        rearmed = set()
        for category in self._suppressed:
            known = category in visibility
            observed = visibility.get(category)
            if known and observed is not False:
                self._absence_counts[category] = 0
                continue
            # A missing visibility entry after a tracker reset is not evidence of
            # absence, nor is a nominally absent category while any unclassified,
            # provisional, or ambiguous track remains.
            if not clear_observation:
                self._absence_counts[category] = 0
                continue
            self._absence_counts[category] = self._absence_counts.get(category, 0) + 1
            if self._absence_counts[category] >= needed:
                rearmed.add(category)
        for category in rearmed:
            self._suppressed.discard(category)
            self._absence_counts.pop(category, None)

    def _sync_cycle(self) -> None:
        operation_id = self.station.operation_id
        if operation_id is None:
            self._cycle_categories.clear()
            self._cycle_operation_id = None
        elif self._cycle_operation_id is not None and operation_id != self._cycle_operation_id:
            self._cycle_categories.clear()
            self._cycle_operation_id = operation_id
        elif operation_id and self._cycle_operation_id is None:
            # A guide may predate scanning; its categories are unknown to this
            # coordinator and are left in Station's independent guide state.
            self._cycle_operation_id = operation_id

    def _set_connection_error(self, exc: Exception) -> None:
        self._single_candidate = None
        self._single_count = 0
        if self._single_track:
            self._single_track['state'] = 'failed'
        detail = str(exc) or exc.__class__.__name__
        for secret in (self.cfg.section('recognizer').get('api_key', ''),
                       getattr(self.cfg, 'scan_pairing_key', '')):
            if secret:
                detail = detail.replace(secret, '[redactado]')
        self._connection_error = detail[:160]
        configured = self.cfg.section('scan').get('retry_backoff_seconds', _BACKOFF_SECONDS)
        try:
            schedule = tuple(min(15, max(1, int(value))) for value in configured)
            if not schedule:
                schedule = _BACKOFF_SECONDS
        except (TypeError, ValueError):
            schedule = _BACKOFF_SECONDS
        delay = schedule[min(self._backoff_index, len(schedule) - 1)]
        self._backoff_index += 1
        self._retry_at = self.clock() + delay

    def _clear_backoff(self) -> None:
        self._connection_error = None
        self._retry_at = None
        self._backoff_index = 0

    def _discard_current_frame(self) -> None:
        self._single_candidate = None
        self._single_count = 0
        self._single_track = None
        self._frame_jpeg = None
        self._frame_sequence = None
        self._frame_image_size = None
        self._current_crops.clear()

    def _get_correction(self, correction_id: str) -> _FrozenCorrection:
        self._purge_expired_corrections()
        correction = self._corrections.get(correction_id)
        if correction is not None:
            return correction
        if correction_id in self._expired_corrections:
            raise ScanError(410, 'La corrección caducó.')
        raise ScanError(404, 'El recorte congelado no existe.')

    def _purge_expired_corrections(self) -> None:
        now = self.clock()
        for correction_id, correction in list(self._corrections.items()):
            if now >= correction.expires_at_clock:
                self._expired_corrections.append(correction_id)
                self._remove_correction(correction_id)

    def _remove_correction(self, correction_id: str) -> None:
        self._corrections.pop(correction_id, None)
        task = self._correction_expiry_task
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        if task and not task.done() and task is not current_task:
            task.cancel()
        self._correction_expiry_task = None

    def _schedule_correction_expiry(self, correction_id: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def expire() -> None:
            await asyncio.sleep(CORRECTION_TTL_SECONDS)
            if correction_id in self._corrections:
                self._expired_corrections.append(correction_id)
                self._corrections.pop(correction_id, None)
            self._correction_expiry_task = None

        self._correction_expiry_task = loop.create_task(expire())


def _consolidate_detections(detections: list[dict], options: dict) -> list[dict]:
    """Suppress duplicate and contained proposals before temporal association.

    DINO can propose the full instrument, its display and its controls under
    different labels. Let Qwen classify the best proposal for that region.
    Separate instruments should be placed next to each other, without overlap.
    """
    iou_threshold = float(options.get('detection_nms_iou', 0.45))
    containment_threshold = float(options.get('detection_containment', 0.9))
    selected = []
    for item in sorted(detections, key=lambda value: value['detector_score'], reverse=True):
        box = item['box']
        area = (box[2] - box[0]) * (box[3] - box[1])
        duplicate = False
        for old in selected:
            other = old['box']
            other_area = (other[2] - other[0]) * (other[3] - other[1])
            intersection = max(0, min(box[2], other[2]) - max(box[0], other[0])) * max(
                0, min(box[3], other[3]) - max(box[1], other[1]))
            if (intersection / (area + other_area - intersection) >= iou_threshold
                    or intersection / min(area, other_area) >= containment_threshold):
                duplicate = True
                break
        if not duplicate:
            selected.append(item)
    return selected


def _normalize_scan_jpeg(data: bytes, max_side: int) -> tuple[bytes, tuple[int, int]]:
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
            image = image.convert('RGB')
            image.thumbnail((max_side, max_side))
            size = image.size
            output = io.BytesIO()
            image.save(output, 'JPEG', quality=88)
            return output.getvalue(), size
    except Exception as exc:
        raise ScanError(400, 'El cuadro no es una imagen válida.') from exc


def _crop_jpeg(data: bytes, box: list[int], image_size: tuple[int, int]) -> bytes:
    width, height = image_size
    x1, y1, x2, y2 = box
    pad_x = max(2, round((x2 - x1) * .04))
    pad_y = max(2, round((y2 - y1) * .04))
    crop_box = (max(0, x1 - pad_x), max(0, y1 - pad_y),
                min(width, x2 + pad_x), min(height, y2 + pad_y))
    with Image.open(io.BytesIO(data)) as image:
        output = io.BytesIO()
        image.crop(crop_box).convert('RGB').save(output, 'JPEG', quality=92)
        return output.getvalue()


def _make_detection_crops(data: bytes, image_size: tuple[int, int],
                          detections: list[dict]) -> dict[tuple[int, int, int, int], dict]:
    crops = {}
    for detection in detections[:MAX_TRACKS]:
        box = tuple(detection['box'])
        crops[box] = {'jpeg': _crop_jpeg(data, list(box), image_size)}
    return crops
