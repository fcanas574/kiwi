import copy
import io
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from test_station import cfg, jpeg_bytes
from kiwi_pi.app import create_app
from kiwi_pi.recognizer import MockRecognizer


def obj(id, category, accepted=True, box=None):
    return {'id': id, 'box': box or [0, 0, 20, 20], 'crop_box': box or [0, 0, 20, 20],
            'detector_score': .5, 'category': category, 'recognized': accepted,
            'recognition': {'candidate': category, 'recognized': accepted,
                            'similarity': .95, 'margin': .8, 'reason': 'fixture', 'ranking': []}}


def response(objects):
    return {'mode': 'grounding_dino_qwen', 'image_size': [64, 48], 'objects': objects}


def client_for(cfg, monkeypatch, payload):
    async def predict(self, jpeg): return copy.deepcopy(payload)
    monkeypatch.setattr(MockRecognizer, 'predict', predict)
    return TestClient(create_app(cfg))


def upload(client):
    return client.post('/api/upload', files={'file': ('a.jpg', jpeg_bytes(), 'image/jpeg')}).json()


def test_mixed_objects_light_unique_accepted_slots(cfg, monkeypatch):
    payload = response([obj(1, 'digital_multimeter'), obj(2, 'servo_motor'),
                        obj(3, 'otros', False), obj(4, 'digital_multimeter'), obj(5, 'capacitors')])
    with client_for(cfg, monkeypatch, payload) as client:
        photo = upload(client)
        result = client.post('/api/recognize', json={'photo_id': photo['photo_id']}).json()
        assert len(result['objects']) == 5
        assert [s['id'] for s in result['slots']] == [1, 7]
        assert [o['status'] for o in result['objects']] == ['accepted', 'accepted', 'rejected', 'accepted', 'rejected']
        state = client.get('/api/status').json()
        assert state['guide']['operation_id'] == result['operation_id']
        assert state['guide']['pending'] == [1, 7]


@pytest.mark.parametrize('objects', [[], [obj(1, 'otros', False)]])
def test_empty_or_rejected_list_lights_nothing(cfg, monkeypatch, objects):
    with client_for(cfg, monkeypatch, response(objects)) as client:
        photo = upload(client)
        result = client.post('/api/recognize', json={'photo_id': photo['photo_id']}).json()
        assert result['status'] == 'rejected'
        assert all(s['led'] == 'off' for s in client.get('/api/status').json()['slots'])


@pytest.mark.parametrize('payload', [response([obj(1, 'servo_motor'), obj(1, 'solder')]),
    response([obj(1, 'servo_motor', box=[0, 0, 70, 20])]),
    response([obj(1, 'servo_motor', box=[0, 0, float('nan'), 20])]),
    {'candidate': 'servo_motor', 'recognized': True},
    response([dict(obj(1, 'servo_motor'), recognized='false')])])
def test_malformed_result_never_activates_leds(cfg, monkeypatch, payload):
    with client_for(cfg, monkeypatch, payload) as client:
        photo = upload(client)
        result = client.post('/api/recognize', json={'photo_id': photo['photo_id']}).json()
        assert result['status'] == 'error'
        assert all(s['led'] == 'off' for s in client.get('/api/status').json()['slots'])


def test_qwen_rejection_cannot_be_overridden_by_station_threshold(cfg, monkeypatch):
    cfg.raw['decision'].update(min_similarity=.1, min_margin=.01)
    with client_for(cfg, monkeypatch, response([obj(1, 'servo_motor', False)])) as client:
        photo = upload(client)
        assert client.post('/api/recognize', json={'photo_id': photo['photo_id']}).json()['status'] == 'rejected'


def test_feedback_saves_only_selected_crop(cfg, monkeypatch):
    with client_for(cfg, monkeypatch, response([obj(1, 'digital_multimeter'), obj(2, 'servo_motor')])) as client:
        photo = upload(client)
        client.post('/api/recognize', json={'photo_id': photo['photo_id']})
        body = {'photo_id': photo['photo_id'], 'object_id': 2, 'actual': 'solder'}
        assert client.post('/api/reject', json=body).status_code == 200
        files = list((cfg.data_dir / 'feedback' / 'solder').glob('*.jpg'))
        assert len(files) == 1
        assert Image.open(files[0]).size == (20, 20)
        body['object_id'] = 99
        assert client.post('/api/reject', json=body).status_code == 404
        body['photo_id'] = 'expired'
        assert client.post('/api/reject', json=body).status_code == 404
        assert len(list((cfg.data_dir / 'feedback' / 'solder').glob('*.jpg'))) == 1


@pytest.mark.parametrize('crop', [[0, 0, 64, 48], [30, 25, 50, 45], [.1, .1, .4, .4]])
def test_incompatible_crop_is_rejected_before_guidance(cfg, monkeypatch, crop):
    item = obj(1, 'servo_motor')
    item['crop_box'] = crop
    with client_for(cfg, monkeypatch, response([item, obj(2, 'solder')])) as client:
        photo = upload(client)
        result = client.post('/api/recognize', json={'photo_id': photo['photo_id']}).json()
        assert result['status'] == 'error'
        assert all(s['led'] == 'off' for s in client.get('/api/status').json()['slots'])


def test_old_unrecognized_photo_cannot_cancel_new_operation(cfg, monkeypatch):
    with client_for(cfg, monkeypatch, response([obj(1, 'servo_motor')])) as client:
        old = upload(client)
        current = upload(client)
        result = client.post('/api/recognize', json={'photo_id': current['photo_id']}).json()
        assert client.post('/api/reject', json={'photo_id': old['photo_id'], 'actual': 'otros'}).status_code == 409
        assert client.get('/api/status').json()['guide']['operation_id'] == result['operation_id']


def test_gradio_client_reads_json_output(cfg, monkeypatch):
    from kiwi_pi.recognizer import GradioRecognizer
    import types, sys
    payload = response([obj(1, 'servo_motor')])
    monkeypatch.setitem(sys.modules, 'gradio_client', types.SimpleNamespace(handle_file=lambda file: file))
    client = GradioRecognizer(cfg)
    monkeypatch.setattr(client, '_get_client', lambda: types.SimpleNamespace(predict=lambda *a, **k: ('photo', [], payload)))
    assert client._predict_sync(jpeg_bytes()) is payload


def test_late_cancel_from_old_photo_does_not_stop_current_guide(cfg, monkeypatch):
    with client_for(cfg, monkeypatch, response([obj(1, 'servo_motor')])) as client:
        old = upload(client); current = upload(client)
        result = client.post('/api/recognize', json={'photo_id': current['photo_id']}).json()
        assert client.post('/api/next', json={'photo_id': old['photo_id']}).status_code == 409
        assert client.get('/api/status').json()['guide']['operation_id'] == result['operation_id']
