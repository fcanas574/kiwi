"""Verify the phone/station split, latest-frame behavior and clean test cycles."""
import asyncio
import io
from threading import Event

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from kiwi_pi.app import create_app
from kiwi_pi.recognizer import MockRecognizer
from test_station import cfg as station_cfg


@pytest.fixture
def cfg(station_cfg):
    station_cfg.scan_pairing_key = 'pair-webcam'
    station_cfg.raw['server']['allow_remote_admin'] = False
    return station_cfg


def jpeg(color='blue'):
    buffer = io.BytesIO()
    Image.new('RGB', (160, 120), color).save(buffer, 'JPEG')
    return buffer.getvalue()


def upload(client, color='blue'):
    return client.post('/api/scan/webcam/frame', files={'file': ('frame.jpg', jpeg(color), 'image/jpeg')})


def test_station_controls_and_private_frames_are_local_only(cfg):
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver', client=('127.0.0.1', 1000)) as station:
        phone = TestClient(app, base_url='https://testserver', client=('192.168.1.22', 1001))
        try:
            assert 'station.js' in station.get('/').text
            assert 'phone.js' in phone.get('/').text
            assert phone.get('/station').status_code == 403
            assert phone.get('/api/station/connection').status_code == 403
            assert phone.get('/api/station/webcam/live.jpg').status_code == 403
            assert upload(phone).status_code == 401
            assert phone.post('/api/scan/pair', json={'code':'pair-webcam'}).status_code == 200
            assert upload(phone).status_code == 200
            assert app.state.scan.snapshot()['capture_state'] == 'paused'
            assert phone.post('/api/station/scan/start').status_code == 403
            assert station.post('/api/station/scan/start').status_code == 200
            assert station.post('/api/station/scan/pause').status_code == 200
            assert station.post('/api/station/scan/finish').status_code == 200
        finally:
            phone.close()


def test_webcam_freshness_latest_frame_and_new_owner(cfg):
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver', client=('127.0.0.1', 1000)) as client:
        assert client.post('/api/station/scan/start').status_code == 409
        client.post('/api/scan/pair', json={'code':'pair-webcam'})
        upload(client, 'blue'); upload(client, 'red')
        bridge = app.state.webcam
        assert bridge.revision == 2
        with Image.open(io.BytesIO(bridge.jpeg)) as image:
            assert image.getpixel((10, 10))[0] > 200
        other = TestClient(app, base_url='https://testserver')
        try:
            other.post('/api/scan/pair', json={'code':'pair-webcam'})
            assert upload(other).status_code == 409
            bridge.received_at -= 5
            assert not bridge.online()
            assert client.post('/api/station/scan/start').status_code == 409
            assert upload(other).status_code == 200
        finally:
            other.close()


def test_finish_clears_leds_and_rearms_same_object(cfg):
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver', client=('127.0.0.1', 1000)) as client:
        scan = app.state.scan
        scan.station.add_guides([cfg.slot_by_id(1)], 'test-cycle')
        scan._suppressed.add(cfg.slot_by_id(1).category)
        scan._absence_counts[cfg.slot_by_id(1).category] = 2
        response = client.post('/api/station/scan/finish')
        assert response.status_code == 200
        assert response.json()['suppressed_categories'] == []
        assert scan.station.pending == set()
        assert all(slot['led'] == 'off' for slot in scan.station.snapshot()['slots'])
        assert scan._absence_counts == {}


def test_finish_during_inference_discards_stale_results_without_blocking_camera(cfg, monkeypatch):
    started, release = Event(), Event()
    original = MockRecognizer.detect

    async def blocked(self, data, correlation):
        started.set()
        await asyncio.to_thread(release.wait, 3)
        return await original(self, data, correlation)

    monkeypatch.setattr(MockRecognizer, 'detect', blocked)
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver', client=('127.0.0.1', 1000)) as client:
        client.post('/api/scan/pair', json={'code':'pair-webcam'})
        upload(client)
        client.post('/api/station/scan/start')
        try:
            assert started.wait(2)
            # A model request must not stop camera ingestion or local controls.
            assert upload(client, 'red').status_code == 200
            assert client.post('/api/station/scan/finish').status_code == 200
        finally:
            release.set()
        client.portal.call(asyncio.sleep, 0.2)
        assert app.state.webcam.analyzed is None
        assert app.state.scan.station.operation_id is None
        assert app.state.scan.snapshot()['capture_state'] == 'paused'


def test_phone_page_has_no_scan_controls(cfg):
    with TestClient(create_app(cfg), base_url='https://testserver') as client:
        page = client.get('/camera').text
        assert 'Iniciar escaneo' not in page
        assert 'Finalizar' not in page
        script = client.get('/static/phone.js').text
        assert '/api/station/' not in script
        assert '/api/scan/start' not in script


def test_lost_camera_pauses_and_invalidates_pending_inference(cfg, monkeypatch):
    started, release = Event(), Event()
    original = MockRecognizer.detect

    async def blocked(self, data, correlation):
        started.set()
        await asyncio.to_thread(release.wait, 3)
        return await original(self, data, correlation)

    monkeypatch.setattr(MockRecognizer, 'detect', blocked)
    app = create_app(cfg)
    with TestClient(app, base_url='https://testserver', client=('127.0.0.1', 1000)) as client:
        client.post('/api/scan/pair', json={'code':'pair-webcam'})
        upload(client)
        first = client.post('/api/station/scan/start').json()
        try:
            assert started.wait(2)
            app.state.webcam.received_at -= 5
            client.portal.call(asyncio.sleep, 0.6)
            paused = app.state.scan.snapshot()
            assert paused['capture_state'] == 'paused'
            assert paused['generation'] > first['generation']
        finally:
            release.set()
        client.portal.call(asyncio.sleep, 0.2)
        assert app.state.scan.station.operation_id is None
        assert app.state.webcam.analyzed is None
