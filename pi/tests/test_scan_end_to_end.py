"""Full continuous-scan journey through the real app and its HTTP routes."""
from __future__ import annotations

import asyncio
import io
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
from PIL import Image

from kiwi_pi.app import create_app
from kiwi_pi.recognizer import MockRecognizer
from test_station import cfg as station_cfg


class ControlledClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = 0.5) -> None:
        self.now += seconds


class _Gate:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()


class ControlledRecognizer(MockRecognizer):
    """Mock Colab endpoints with scripted objects, failures, and held replies."""

    name = 'controlled-colab'

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self.active_object_ids = {1}
        self.categories = {1: 'digital_multimeter', 2: 'servo_motor'}
        self.fail_next_detection = False
        self.next_detection_gate: _Gate | None = None

    def hold_next_detection(self) -> _Gate:
        gate = _Gate()
        self.next_detection_gate = gate
        return gate

    async def detect(self, jpeg: bytes, correlation: dict) -> dict:
        gate, self.next_detection_gate = self.next_detection_gate, None
        if gate:
            gate.started.set()
            await asyncio.to_thread(gate.release.wait, 5)
        if self.fail_next_detection:
            self.fail_next_detection = False
            raise RuntimeError('Colab desconectado durante la prueba')

        with Image.open(io.BytesIO(jpeg)) as image:
            width, height = image.size
        objects = []
        for object_id in sorted(self.active_object_ids):
            column, row = (object_id - 1) % 4, (object_id - 1) // 4
            box = [int(column * width / 4), int(row * height / 3),
                   int((column + 1) * width / 4), int((row + 1) * height / 3)]
            objects.append({'id': object_id, 'box': box,
                            'detector_label': self.categories[object_id],
                            'detector_score': 0.95})
        return {'correlation': correlation,
                'result': {'image_size': [width, height], 'objects': objects,
                           'raw_detections': len(objects), 'detection_seconds': 0.01}}

    async def classify(self, jpeg: bytes, correlation: dict) -> dict:
        category = self.categories[int(correlation['object_id'])]
        result = {'candidate': category, 'recognized': True, 'similarity': 0.95,
                  'margin': 0.8, 'reason': 'Resultado controlado de Colab.',
                  'ranking': [{'object': category, 'similarity': 0.95}]}
        return {'correlation': correlation, 'result': result}


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new('RGB', (120, 90), 'navy').save(output, 'JPEG')
    return output.getvalue()


def _post_frame(client: TestClient, session: dict, sequence: int, jpeg: bytes) -> dict:
    response = client.post('/api/scan/frame', data={
        'session_id': session['session_id'],
        'generation': str(session['generation']),
        'sequence': str(sequence),
    }, files={'file': ('frame.jpg', jpeg, 'image/jpeg')})
    assert response.status_code == 200, response.text
    return response.json()


def _status(client: TestClient) -> dict:
    response = client.get('/api/status')
    assert response.status_code == 200
    return response.json()


def _wait_for(client: TestClient, predicate, message: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    state = _status(client)
    while time.monotonic() < deadline:
        if predicate(state):
            return state
        time.sleep(0.01)
        state = _status(client)
    raise AssertionError(f'{message}; último estado: {state}')


def _led(state: dict, slot_id: int) -> str:
    return next(slot['led'] for slot in state['slots'] if slot['id'] == slot_id)


def _set_sensor(client: TestClient, slot_id: int, occupied: bool) -> None:
    response = client.post(f'/api/sim/slots/{slot_id}', json={'occupied': occupied})
    assert response.status_code == 200, response.text


def test_real_app_scan_journey_survives_sensors_outages_and_stale_replies(
        station_cfg, monkeypatch):
    cfg = station_cfg
    cfg.scan_pairing_key = 'end-to-end-pairing-code'
    cfg.raw['scan'].update(stable_samples=3, stable_seconds=1.0, retry_seconds=0,
                           rearm_samples=3, max_gap_seconds=30,
                           retry_backoff_seconds=[1])
    cfg.raw['guide']['auto_reset_seconds'] = 30
    cfg.raw['leds']['ok_seconds'] = 30

    recognizer = ControlledRecognizer(cfg)
    monkeypatch.setattr('kiwi_pi.app.make_recognizer', lambda _: recognizer)
    app = create_app(cfg)
    clock = ControlledClock()
    jpeg = _jpeg()

    with TestClient(app, base_url='https://testserver') as client:
        scan = app.state.scan
        station = scan.station
        scan.clock = clock

        paired = client.post('/api/scan/pair', json={'code': cfg.scan_pairing_key})
        assert paired.status_code == 200
        started = client.post('/api/scan/start', json={}).json()

        # Three stable observations allow Qwen to answer provisionally; only
        # the following compatible DINO observation may light the first LED.
        for sequence in (1, 2, 3):
            clock.advance(0.5)
            state_after_qwen = _post_frame(client, started, sequence, jpeg)
        assert state_after_qwen['guide']['targets'] == []
        assert state_after_qwen['tracks'][0]['state'] == 'provisional'
        provisional_status = _status(client)
        assert provisional_status['guide']['targets'] == []
        assert _led(provisional_status, 1) == 'off'

        clock.advance(0.5)
        _post_frame(client, started, 4, jpeg)
        state_after_confirmation_frame = _status(client)
        assert state_after_confirmation_frame['guide']['pending'] == [1]
        assert _led(state_after_confirmation_frame, 1) == 'guide'
        first_operation = state_after_confirmation_frame['guide']['operation_id']

        # Hold a real /api/scan/frame request at the recognizer boundary. The
        # mock sensor API and monitor must confirm slot 1 while Colab is slow.
        slow_detection = recognizer.hold_next_detection()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending_frame = pool.submit(_post_frame, client, started, 5, jpeg)
            try:
                assert slow_detection.started.wait(2)
                assert _status(client)['scan']['busy'] is True
                _set_sensor(client, 1, True)
                state_after_first_sensor = _wait_for(
                    client,
                    lambda state: state['guide']['confirmed'] == [1],
                    'el sensor no confirmó la primera devolución mientras Colab esperaba')
                assert state_after_first_sensor['scan']['busy'] is True
                assert state_after_first_sensor['guide']['phase'] == 'returned'
                assert next(slot for slot in state_after_first_sensor['slots']
                            if slot['id'] == 1)['occupied'] is True
                assert _led(state_after_first_sensor, 1) == 'ok'
            finally:
                slow_detection.release.set()
            pending_frame.result(timeout=3)

        # A second stable object joins the same guide operation and keeps the
        # earlier green confirmation intact.
        recognizer.active_object_ids = {1, 2}
        for sequence in (6, 7, 8, 9):
            clock.advance(0.5)
            _post_frame(client, started, sequence, jpeg)
        state_after_new_target = _status(client)
        assert state_after_new_target['guide']['operation_id'] == first_operation
        assert [item['id'] for item in state_after_new_target['guide']['targets']] == [1, 7]
        assert state_after_new_target['guide']['confirmed'] == [1]
        assert state_after_new_target['guide']['pending'] == [7]
        assert _led(state_after_new_target, 1) == 'ok'
        assert _led(state_after_new_target, 7) == 'guide'

        # The final sensor finishes this cycle while the camera session keeps
        # running. The short green timer is used only after this assertion.
        station.auto_reset = 0.05
        station.ok_seconds = 0.05
        _set_sensor(client, 7, True)
        state_after_final_sensor = _wait_for(
            client,
            lambda state: state['guide']['phase'] == 'returned',
            'el ciclo no terminó después del sensor final')
        assert state_after_final_sensor['guide']['confirmed'] == [1, 7]
        assert state_after_final_sensor['scan']['capture_state'] == 'running'
        assert all(next(slot for slot in state_after_final_sensor['slots']
                       if slot['id'] == slot_id)['occupied'] is True for slot_id in (1, 7))
        assert _led(state_after_final_sensor, 1) == 'ok'
        assert _led(state_after_final_sensor, 7) == 'ok'

        _wait_for(client, lambda state: state['guide']['phase'] == 'idle',
                  'el ciclo verde no se cerró')
        _set_sensor(client, 1, False)
        _wait_for(client, lambda state: next(
            slot for slot in state['slots'] if slot['id'] == 1)['occupied'] is False,
            'el sensor 1 no registró la ausencia')

        # Only three valid DINO absences rearm both categories. Then the same
        # category can start a genuinely new return cycle.
        recognizer.active_object_ids = set()
        for sequence in (10, 11, 12):
            clock.advance(0.5)
            _post_frame(client, started, sequence, jpeg)
        state_after_valid_absence = _status(client)
        assert state_after_valid_absence['scan']['suppressed_categories'] == []

        recognizer.active_object_ids = {1}
        for sequence in (13, 14, 15, 16):
            clock.advance(0.5)
            _post_frame(client, started, sequence, jpeg)
        state_after_new_cycle = _status(client)
        assert state_after_new_cycle['guide']['operation_id'] != first_operation
        assert [item['id'] for item in state_after_new_cycle['guide']['targets']] == [1]
        assert state_after_new_cycle['guide']['pending'] == [1]

        # Colab outage leaves the guide and suppression intact. A phone
        # reconnect starts a new generation while retaining station progress.
        recognizer.fail_next_detection = True
        clock.advance(0.5)
        failed = _post_frame(client, started, 17, jpeg)
        assert failed['connection_error']
        assert failed['guide']['pending'] == [1]
        assert 'digital_multimeter' in failed['suppressed_categories']

        paused = client.post('/api/scan/pause', json={
            'session_id': started['session_id']})
        assert paused.status_code == 200
        reconnected = client.post('/api/scan/start', json={
            'session_id': started['session_id']}).json()
        assert reconnected['generation'] > started['generation']
        after_reconnect = _status(client)
        assert after_reconnect['guide']['pending'] == [1]
        assert after_reconnect['guide']['operation_id'] == state_after_new_cycle['guide']['operation_id']
        assert 'digital_multimeter' in after_reconnect['scan']['suppressed_categories']
        assert _led(after_reconnect, 1) == 'guide'

        clock.advance(2)
        late_detection = recognizer.hold_next_detection()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending_late_frame = pool.submit(_post_frame, client, reconnected, 1, jpeg)
            try:
                assert late_detection.started.wait(2)
                assert _status(client)['scan']['busy'] is True
                _set_sensor(client, 1, True)
                state_while_late = _wait_for(
                    client,
                    lambda state: state['guide']['phase'] == 'returned',
                    'el sensor no avanzó mientras la respuesta de Colab estaba pendiente')
                assert state_while_late['scan']['busy'] is True
                assert _led(state_while_late, 1) == 'ok'

                client.post('/api/scan/pause', json={
                    'session_id': reconnected['session_id']}).raise_for_status()
                latest_session = client.post('/api/scan/start', json={
                    'session_id': reconnected['session_id']}).json()
                assert latest_session['generation'] > reconnected['generation']
            finally:
                late_detection.release.set()
            late_result = pending_late_frame.result(timeout=3)

        assert late_result['discarded'] is True
        final_state = _status(client)
        assert final_state['scan']['capture_state'] == 'running'
        assert final_state['guide']['phase'] == 'returned'
        assert final_state['guide']['confirmed'] == [1]
        assert _led(final_state, 1) == 'ok'
