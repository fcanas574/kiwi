import io
import json

from fastapi.testclient import TestClient
from PIL import Image

from server import create_app

KEY = 'local-test-only-key-1234'
HEADERS = {'Authorization': 'Bearer ' + KEY}


class FakeEngine:
    def identify(self, image):
        return {**self.locate(image), 'mode': 'grounding_dino_qwen'}

    def locate(self, image):
        return {'image_size': list(image.size), 'objects': [], 'raw_detections': 0, 'detection_seconds': 0}

    def classify_many(self, images):
        return [self.classify(image) for image in images]

    def classify(self, image):
        return {'candidate': 'otros', 'recognized': False, 'reason': 'fixture',
                'similarity': .95, 'margin': .9,
                'ranking': [{'object': 'otros', 'similarity': .95}]}


def jpeg():
    data = io.BytesIO()
    Image.new('RGB', (64, 48), 'white').save(data, 'JPEG')
    return data.getvalue()


def test_auth_split_endpoints_and_batch_correlation():
    with TestClient(create_app(FakeEngine, KEY)) as client:
        assert client.get('/health').status_code == 401
        health = client.get('/health', headers=HEADERS).json()
        assert health['capabilities']['classify_batch']
        manual = client.post('/predict', headers=HEADERS,
            files={'file': ('a.jpg', jpeg(), 'image/jpeg')})
        assert manual.status_code == 200
        assert manual.json()['mode'] == 'grounding_dino_qwen'
        assert manual.json()['objects'] == []
        for operation in ('detect', 'classify'):
            response = client.post('/' + operation, headers=HEADERS,
                files={'file': ('a.jpg', jpeg(), 'image/jpeg')}, data={'correlation': '{"frame_id":5}'})
            assert response.status_code == 200
            assert response.json()['correlation'] == {'frame_id': 5}
        metadata = [{'track_id': i, 'generation': 7} for i in range(1, 4)]
        response = client.post('/classify-batch', headers=HEADERS,
            files=[('files', ('a.jpg', jpeg(), 'image/jpeg'))] * 3,
            data={'correlations': json.dumps(metadata)})
        assert response.status_code == 200
        assert [item['correlation'] for item in response.json()['items']] == metadata
        assert all(not item['result']['recognized'] for item in response.json()['items'])


def test_batch_bounds_and_invalid_images():
    with TestClient(create_app(FakeEngine, KEY)) as client:
        for count in (1, 4):
            response = client.post('/classify-batch', headers=HEADERS,
                files=[('files', ('a.jpg', jpeg(), 'image/jpeg'))] * count,
                data={'correlations': '[{},{}]'})
            assert response.status_code == 422
        assert client.post('/classify', headers=HEADERS,
            files={'file': ('a.jpg', b'invalid', 'image/jpeg')}).status_code == 422
        assert client.post('/classify', headers=HEADERS,
            files={'file': ('a.jpg', b'x' * (2 * 1024 * 1024 + 1), 'image/jpeg')}).status_code == 413
