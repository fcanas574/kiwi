import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kiwi_pi.__main__ import MOCK
from kiwi_pi.app import create_app
from kiwi_pi.config import EXAMPLE_CONFIG, load_config
from kiwi_pi.recognizer import decide


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv('KIWI_DATA_DIR', str(tmp_path))
    overrides = {**MOCK, 'sensors': {'driver': 'mock', 'debounce_ms': 0, 'poll_ms': 10},
                 'guide': {'auto_reset_seconds': 0.3}, 'leds': {'driver': 'mock', 'ok_seconds': 0.2}}
    return load_config(EXAMPLE_CONFIG, overrides)   # no depende del config.yaml local


def api_result(candidate, recognized=True, ranking=None):
    ranking = ranking or [{'object': candidate, 'similarity': 0.9}, {'object': 'oscilloscope', 'similarity': 0.6}]
    return {'candidate': candidate, 'recognized': recognized, 'similarity': ranking[0]['similarity'],
            'margin': 0.3, 'reason': 'x', 'ranking': ranking}


def jpeg_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (64, 48), 'red').save(buffer, 'JPEG')
    return buffer.getvalue()


def wait_for(client, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get('/api/status').json()
        if predicate(status):
            return status
        time.sleep(0.03)
    raise AssertionError(f'condición no cumplida: {status["guide"]}')


def test_decide_rejects_tools_outside_station(cfg):
    decision = decide(cfg, api_result('multimeter_box'))
    assert not decision.accepted


def test_decide_respects_api_and_test_mode(cfg):
    assert decide(cfg, api_result('oscilloscope')).accepted
    assert not decide(cfg, api_result('oscilloscope', recognized=False)).accepted
    cfg.raw['decision']['test_mode'] = True
    decision = decide(cfg, api_result('oscilloscope', recognized=False))
    assert decision.accepted and decision.test_mode


def test_decide_station_thresholds_use_only_station_categories(cfg):
    cfg.raw['decision'].update(min_similarity=0.5, min_margin=0.1)
    ranking = [{'object': 'oscilloscope', 'similarity': 0.70},
               {'object': 'capacitors', 'similarity': 0.69},      # fuera de la estación: no cuenta
               {'object': 'digital_multimeter', 'similarity': 0.55}]
    decision = decide(cfg, api_result('oscilloscope', recognized=False, ranking=ranking))
    assert decision.accepted and decision.margin == pytest.approx(0.15)
    ranking[2]['similarity'] = 0.65
    assert not decide(cfg, api_result('oscilloscope', ranking=ranking)).accepted


def test_full_flow_correct_and_wrong_slot(cfg):
    app = create_app(cfg)
    with TestClient(app) as client:
        photo = client.post('/api/upload', files={'file': ('a.jpg', jpeg_bytes(), 'image/jpeg')}).json()
        assert client.get(photo['url']).status_code == 200

        client.post('/api/sim/recognizer', params={'category': 'function_generator'})
        result = client.post('/api/recognize', json={'photo_id': photo['photo_id']}).json()
        assert result['status'] == 'accepted' and result['slot']['id'] == 3

        status = client.get('/api/status').json()
        assert status['guide']['phase'] == 'guiding'
        assert status['slots'][2]['led'] == 'guide'

        # Se coloca en la posición 5: error
        client.post('/api/sim/slots/5', json={'occupied': True})
        status = wait_for(client, lambda s: s['guide']['phase'] == 'wrong_slot')
        assert status['guide']['wrong'] == 5 and status['slots'][4]['led'] == 'wrong'

        # La retiran y la ponen en la 3: correcto
        client.post('/api/sim/slots/5', json={'occupied': False})
        wait_for(client, lambda s: s['guide']['phase'] == 'guiding')
        client.post('/api/sim/slots/3', json={'occupied': True})
        status = wait_for(client, lambda s: s['guide']['phase'] == 'returned')
        assert status['slots'][2]['led'] == 'ok'

        # Reinicio automático
        status = wait_for(client, lambda s: s['guide']['phase'] == 'idle')
        assert all(slot['led'] == 'off' for slot in status['slots'])


def test_reject_saves_feedback_and_turns_off(cfg):
    app = create_app(cfg)
    with TestClient(app) as client:
        photo = client.post('/api/upload', files={'file': ('a.jpg', jpeg_bytes(), 'image/jpeg')}).json()
        client.post('/api/sim/recognizer', params={'category': 'oscilloscope'})
        client.post('/api/recognize', json={'photo_id': photo['photo_id']})
        client.post('/api/reject', json={'photo_id': photo['photo_id'], 'actual': 'otros'})
        status = client.get('/api/status').json()
        assert status['guide']['phase'] == 'idle'
        assert list((cfg.data_dir / 'feedback' / 'otros').glob('*.jpg'))
        assert client.post('/api/reject', json={'photo_id': photo['photo_id'], 'actual': 'x'}).status_code == 400
        assert (cfg.data_dir / 'recognitions.csv').read_text().count('\n') == 2


def test_unknown_object_does_not_light(cfg):
    app = create_app(cfg)
    with TestClient(app) as client:
        photo = client.post('/api/upload', files={'file': ('a.jpg', jpeg_bytes(), 'image/jpeg')}).json()
        client.post('/api/sim/recognizer', params={'category': 'capacitors'})
        result = client.post('/api/recognize', json={'photo_id': photo['photo_id']}).json()
        assert result['status'] == 'rejected'
        assert all(slot['led'] == 'off' for slot in client.get('/api/status').json()['slots'])


def test_admin_routes_are_local_only(cfg):
    cfg.raw['server']['allow_remote_admin'] = False
    app = create_app(cfg)
    with TestClient(app) as client:   # el host del cliente de pruebas no es localhost
        assert client.post('/api/leds/test').status_code == 403
        assert client.put('/api/endpoint', json={'url': 'https://x.ngrok-free.app/predict'}).status_code == 403


def test_import_feedback_into_references(tmp_path):
    from kiwi_pi.dataset import build_zip, import_feedback
    dataset = tmp_path / 'dataset_prepared'
    (dataset / 'references' / 'otros').mkdir(parents=True)
    Image.new('RGB', (8, 8)).save(dataset / 'references' / 'otros' / 'otros_003.jpg')
    (dataset / 'manifest.csv').write_text('cabecera\n')
    feedback = tmp_path / 'feedback'
    (feedback / 'otros').mkdir(parents=True)
    (feedback / 'otros' / 'a.jpg').write_bytes(jpeg_bytes())
    (feedback / 'otros' / 'a.json').write_text('{"actual": "otros"}')
    (feedback / 'otros' / 'viejo.jpg').write_bytes(jpeg_bytes())       # formato anterior: etiqueta sin confirmar
    (feedback / 'otros' / 'viejo.json').write_text('{"category": "otros"}')
    (feedback / 'sin_etiqueta' / 'servo_motor').mkdir(parents=True)

    assert import_feedback(feedback, dataset) == {'otros': 1}
    assert (dataset / 'references' / 'otros' / 'otros_004.jpg').exists()
    assert (feedback / '_importado' / 'otros' / 'a.json').exists()
    assert build_zip(dataset)[1] == 2
