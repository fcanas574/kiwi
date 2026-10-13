import io
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kiwi_pi.__main__ import MOCK
from kiwi_pi.app import create_app
from kiwi_pi.config import EXAMPLE_CONFIG, load_config
from kiwi_pi.scan import ScanError
from kiwi_pi.scan_api import PAIR_COOKIE
from kiwi_pi.recognizer import MockRecognizer
from test_station import cfg as station_cfg


@pytest.fixture
def cfg(station_cfg):
    station_cfg.scan_pairing_key = 'test-pairing-code'
    return station_cfg


def jpeg_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (100, 100), 'blue').save(buffer, 'JPEG')
    return buffer.getvalue()


@pytest.mark.parametrize('expired', [False, True])
def test_pairing_recovery_preserves_guides_and_invalidates_old_generation(cfg, monkeypatch, expired):
    now = [1000.0]
    monkeypatch.setattr('kiwi_pi.scan_api.time.time', lambda: now[0])
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver') as client:
        client.post('/api/scan/pair', json={'code': cfg.scan_pairing_key})
        old_token = client.cookies.get(PAIR_COOKIE)
        first = client.post('/api/scan/start', json={}).json()
        scan = app.state.scan
        scan.station.add_guides([cfg.slot_by_id(1)], 'recovery-cycle')
        scan._suppressed.add('servo_motor')
        if expired:
            now[0] += 43_201
            client.cookies.clear()  # Browsers omit an expired cookie.
        assert client.post('/api/scan/pair', json={'code': cfg.scan_pairing_key}).status_code == 200
        recovered = client.post('/api/scan/start', json={'session_id': first['session_id']})
        assert recovered.status_code == 200
        assert recovered.json()['generation'] > first['generation']
        assert scan.station.operation_id == 'recovery-cycle'
        assert scan.station.pending == {1}
        assert scan.snapshot()['suppressed_categories'] == ['servo_motor']
        assert client.post('/api/scan/frame', data={
            'session_id': first['session_id'], 'generation': first['generation'], 'sequence': 1},
            files={'file': ('frame.jpg', jpeg_bytes(), 'image/jpeg')}).status_code == 409
        old = TestClient(app, base_url='https://testserver')
        try:
            old.cookies.set(PAIR_COOKIE, old_token)
            assert old.post('/api/scan/pause', json={'session_id': first['session_id']}).status_code == 401
        finally:
            old.close()


@pytest.mark.parametrize('expired', [False, True])
def test_pairing_recovery_discards_event_blocked_inference(cfg, monkeypatch, expired):
    now = [1000.0]
    monkeypatch.setattr('kiwi_pi.scan_api.time.time', lambda: now[0])
    started, release = Event(), Event()
    original = MockRecognizer.detect

    async def blocked(self, jpeg, correlation):
        started.set()
        await asyncio.to_thread(release.wait, 2)
        return await original(self, jpeg, correlation)

    monkeypatch.setattr(MockRecognizer, 'detect', blocked)
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver') as client:
        client.post('/api/scan/pair', json={'code': cfg.scan_pairing_key})
        first = client.post('/api/scan/start', json={}).json()
        with ThreadPoolExecutor(max_workers=1) as pool:
            inference = pool.submit(client.post, '/api/scan/frame', data={
                'session_id': first['session_id'], 'generation': first['generation'], 'sequence': 1},
                files={'file': ('frame.jpg', jpeg_bytes(), 'image/jpeg')})
            try:
                assert started.wait(2)
                if expired:
                    now[0] += 43_201
                    assert client.post('/api/scan/pause', json={
                        'session_id': first['session_id']}).status_code == 401
                assert client.post('/api/scan/pair', json={'code': cfg.scan_pairing_key}).status_code == 200
                resumed = client.post('/api/scan/start', json={})
                assert resumed.status_code == 200
                assert resumed.json()['generation'] > first['generation']
            finally:
                release.set()
            assert inference.result(timeout=3).json()['detections'] == []
        assert app.state.scan.snapshot()['tracks'] == []
        assert app.state.scan.station.operation_id is None


def test_different_paired_phone_takes_over_only_after_explicit_pause(cfg):
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver') as a:
        b = TestClient(app, base_url='https://testserver')
        try:
            for phone in (a, b):
                assert phone.post('/api/scan/pair', json={'code': cfg.scan_pairing_key}).status_code == 200
            first = a.post('/api/scan/start', json={}).json()
            assert b.post('/api/scan/start', json={}).status_code == 409
            assert a.post('/api/scan/pause', json={'session_id': first['session_id']}).status_code == 200
            second = b.post('/api/scan/start', json={'session_id': first['session_id']})
            assert second.status_code == 200
            assert second.json()['generation'] > first['generation']
            assert a.post('/api/scan/cancel', json={'session_id': first['session_id']}).status_code == 409
        finally:
            b.close()


def test_frozen_correction_cannot_cancel_successor_phone(cfg):
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver') as a:
        b = TestClient(app, base_url='https://testserver')
        try:
            for phone in (a, b):
                phone.post('/api/scan/pair', json={'code': cfg.scan_pairing_key})
            first = a.post('/api/scan/start', json={}).json()
            frame = a.post('/api/scan/frame', data={
                'session_id': first['session_id'], 'generation': first['generation'], 'sequence': 1},
                files={'file': ('frame.jpg', jpeg_bytes(), 'image/jpeg')}).json()
            track = frame['tracks'][0]
            frozen = a.post('/api/scan/correction/prepare', json={
                'session_id': first['session_id'], 'track_id': track['track_id'],
                'revision': track['revision']}).json()
            second = b.post('/api/scan/start', json={'session_id': first['session_id']})
            assert second.status_code == 200
            scan = app.state.scan
            scan.station.add_guides([cfg.slot_by_id(1)], 'successor-cycle')
            before = scan.snapshot()
            result = a.post('/api/scan/correction/submit', json={
                'correction_id': frozen['correction_id'], 'actual': 'solder'})
            assert result.status_code == 409
            assert scan.snapshot() == before
            assert not list((cfg.data_dir / 'feedback').rglob('*.jpg'))
            assert a.get(f"/api/scan/correction/{frozen['correction_id']}/crop").status_code == 200
            assert a.post('/api/scan/correction/cancel', json={
                'correction_id': frozen['correction_id']}).status_code == 200
            assert scan.snapshot() == before
            assert b.post('/api/scan/pause', json={'session_id': first['session_id']}).status_code == 200
        finally:
            b.close()


@pytest.mark.parametrize('pause_before_release', [False, True])
def test_manual_predict_started_before_scan_cannot_install_guide(cfg, monkeypatch, pause_before_release):
    started, release = Event(), Event()
    original = MockRecognizer.predict

    async def blocked(self, jpeg):
        started.set()
        await asyncio.to_thread(release.wait, 2)
        return await original(self, jpeg)

    monkeypatch.setattr(MockRecognizer, 'predict', blocked)
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver') as client:
        photo = client.post('/api/upload', files={'file': ('photo.jpg', jpeg_bytes(), 'image/jpeg')}).json()
        with ThreadPoolExecutor(max_workers=1) as pool:
            prediction = pool.submit(client.post, '/api/recognize', json={'photo_id': photo['photo_id']})
            try:
                assert started.wait(2)
                client.post('/api/scan/pair', json={'code': cfg.scan_pairing_key})
                session = client.post('/api/scan/start', json={})
                assert session.status_code == 200
                if pause_before_release:
                    assert client.post('/api/scan/pause', json={
                        'session_id': session.json()['session_id']}).status_code == 200
            finally:
                release.set()
            assert prediction.result(timeout=3).status_code == 409
        assert app.state.scan.snapshot()['capture_state'] == ('paused' if pause_before_release else 'running')
        assert app.state.scan.station.operation_id is None
        assert not app.state.scan.station.pending


def test_scan_pairing_key_is_distinct_from_colab_key(tmp_path, monkeypatch):
    monkeypatch.setenv('KIWI_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('KIWI_API_KEY', 'colab-api-secret')
    monkeypatch.setenv('KIWI_SCAN_PAIRING_KEY', 'phone-pairing-secret')
    cfg = load_config(EXAMPLE_CONFIG, MOCK)

    assert cfg.section('recognizer')['api_key'] == 'colab-api-secret'
    assert cfg.scan_pairing_key == 'phone-pairing-secret'
    assert 'pairing_key' not in cfg.raw.get('recognizer', {})
    assert cfg.raw.get('scan', {}).get('pairing_key') in (None, '')


def test_unpaired_client_cannot_send_frames(cfg):
    with TestClient(create_app(cfg), base_url='https://testserver') as client:
        response = client.post('/api/scan/frame', data={
            'session_id': 'foreign', 'generation': '1', 'sequence': '1'},
            files={'file': ('frame.jpg', jpeg_bytes(), 'image/jpeg')})
        assert response.status_code == 401


def test_pair_cookie_is_httponly_and_never_returns_secrets(cfg):
    with TestClient(create_app(cfg), base_url='https://testserver') as client:
        wrong = client.post('/api/scan/pair', json={'code': 'wrong'})
        assert wrong.status_code == 401
        paired = client.post('/api/scan/pair', json={'code': 'test-pairing-code'})
        assert paired.status_code == 200
        assert paired.json() == {'paired': True}
        cookie = paired.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'secure' in cookie and 'samesite=strict' in cookie
        assert 'test-pairing-code' not in paired.text
        assert 'KIWI_API_KEY' not in paired.text
        status = client.get('/api/scan/state').json()
        assert 'test-pairing-code' not in str(status)
        assert 'colab-api-secret' not in str(status)


def test_pairing_code_cannot_be_the_colab_api_key(cfg):
    cfg.raw['recognizer']['api_key'] = 'same-secret'
    cfg.scan_pairing_key = 'same-secret'
    with TestClient(create_app(cfg), base_url='https://testserver') as client:
        response = client.post('/api/scan/pair', json={'code': 'same-secret'})
        assert response.status_code == 503


def test_paired_client_starts_pauses_and_resumes_new_generation(cfg):
    with TestClient(create_app(cfg), base_url='https://testserver') as client:
        paired = client.post('/api/scan/pair', json={'code': 'test-pairing-code'})
        assert paired.status_code == 200
        started = client.post('/api/scan/start', json={})
        assert started.status_code == 200
        first = started.json()
        assert first['capture_state'] == 'running'

        app_scan = client.app.state.scan
        with pytest.raises(ScanError) as foreign:
            app_scan.start('another-paired-phone')
        assert foreign.value.status_code == 409

        paused = client.post('/api/scan/pause', json={'session_id': first['session_id']})
        assert paused.status_code == 200
        resumed = client.post('/api/scan/start', json={'session_id': first['session_id']})
        assert resumed.status_code == 200
        assert resumed.json()['generation'] > first['generation']
        canceled = client.post('/api/scan/cancel', json={'session_id': first['session_id']})
        assert canceled.status_code == 200
        assert canceled.json()['capture_state'] == 'paused'


def test_paired_frame_validates_sequence_and_returns_bound_state(cfg):
    with TestClient(create_app(cfg), base_url='https://testserver') as client:
        client.post('/api/scan/pair', json={'code': 'test-pairing-code'})
        started = client.post('/api/scan/start', json={}).json()
        fields = {'session_id': started['session_id'],
                  'generation': str(started['generation']), 'sequence': '1'}
        result = client.post('/api/scan/frame', data=fields,
                             files={'file': ('frame.jpg', jpeg_bytes(), 'image/jpeg')})
        assert result.status_code == 200
        payload = result.json()
        assert payload['session_id'] == started['session_id']
        assert payload['generation'] == started['generation']
        assert payload['last_sequence'] == 1
        assert len(payload['detections']) == 3
        duplicate = client.post('/api/scan/frame', data=fields,
                                files={'file': ('frame.jpg', jpeg_bytes(), 'image/jpeg')})
        assert duplicate.status_code == 409


def test_correction_crop_requires_paired_owner(cfg):
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver') as client:
        client.post('/api/scan/pair', json={'code': 'test-pairing-code'})
        started = client.post('/api/scan/start', json={}).json()
        frame = client.post('/api/scan/frame', data={
            'session_id': started['session_id'], 'generation': str(started['generation']),
            'sequence': '1'}, files={'file': ('frame.jpg', jpeg_bytes(), 'image/jpeg')})
        assert frame.status_code == 200
        track = frame.json()['tracks'][0]
        prepared = client.post('/api/scan/correction/prepare', json={
            'session_id': started['session_id'], 'track_id': track['track_id'],
            'revision': track['revision']})
        assert prepared.status_code == 200

        unpaired = TestClient(app, base_url='https://testserver')
        try:
            assert unpaired.get(f"/api/scan/correction/{prepared.json()['correction_id']}/crop").status_code == 401
        finally:
            unpaired.close()

        crop = client.get(f"/api/scan/correction/{prepared.json()['correction_id']}/crop")
        assert crop.status_code == 200
        assert crop.headers['content-type'] == 'image/jpeg'


def test_admin_led_reset_invalidates_scan_and_preserves_admin_boundary(cfg):
    cfg.raw['server']['allow_remote_admin'] = True
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver') as client:
        client.post('/api/scan/pair', json={'code': 'test-pairing-code'})
        started = client.post('/api/scan/start', json={}).json()
        scan = app.state.scan
        scan.station.add_guides([cfg.slot_by_id(1)], 'continuous-admin')
        before = scan.snapshot()['generation']

        response = client.post('/api/leds/off')

        assert response.status_code == 200
        assert scan.snapshot()['generation'] > before
        assert scan.snapshot()['capture_state'] == 'paused'
        assert scan.station.operation_id is None
        stale = client.post('/api/scan/frame', data={
            'session_id': started['session_id'], 'generation': str(started['generation']),
            'sequence': '1'}, files={'file': ('frame.jpg', jpeg_bytes(), 'image/jpeg')})
        assert stale.status_code == 409

