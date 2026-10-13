import ast
import asyncio
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

import kiwi_pi.recognizer as recognizer_module
from kiwi_pi.recognizer import (GradioRecognizer, MockRecognizer, RecognizerError,
                                RestRecognizer)
from test_station import cfg, jpeg_bytes

NOTEBOOK = Path(__file__).resolve().parents[2] / 'Kiwi_EmbeddingGemma2_Colab.ipynb'


def notebook_function(name):
    notebook = json.loads(NOTEBOOK.read_text())
    for cell in notebook['cells']:
        if cell['cell_type'] != 'code':
            continue
        source = ''.join(cell['source'])
        if source.lstrip().startswith('%'):
            continue
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                node.decorator_list = []
                node.returns = None
                for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                    arg.annotation = None
                node.args.defaults = []
                node.args.kw_defaults = [None] * len(node.args.kw_defaults)
                return compile(ast.Module(body=[node], type_ignores=[]), str(NOTEBOOK), 'exec')
    raise AssertionError(f'Falta la función {name} en el notebook')


def install_notebook_functions(namespace, *names):
    for name in names:
        exec(notebook_function(name), namespace)


def image_upload():
    return SimpleNamespace(file=io.BytesIO(jpeg_bytes()))


def test_split_endpoints_share_lock_and_auth():
    class CountingLock:
        def __init__(self):
            self.entries = 0

        def __enter__(self):
            self.entries += 1
            return self

        def __exit__(self, *args):
            return False

    class Models:
        def locate(self, image):
            return {'image_size': list(image.size), 'objects': [], 'raw_detections': 0,
                    'detection_seconds': 0.0}

        def classify_crop(self, image):
            return {'candidate': 'servo_motor', 'recognized': True, 'similarity': .95,
                    'margin': .8, 'reason': 'fixture', 'ranking': []}

    lock = CountingLock()
    ns = {'API_KEY': 'secret-key', 'model_lock': lock, 'multi_recognizer': Models(),
          'vlm_model': object(), 'HTTPException': HTTPException, 'secrets': __import__('secrets'),
          'json': json, 'io': io, 'Image': Image, 'threading': threading,
          'ImageOps': __import__('PIL.ImageOps', fromlist=['ImageOps'])}
    install_notebook_functions(ns, '_authorize', '_read_image', '_parse_correlation',
                               '_process_scan', 'detect', 'classify')
    correlation = {'generation': 4, 'frame_id': 9, 'object_id': 2}

    with pytest.raises(HTTPException) as detect_error:
        ns['detect'](image_upload(), json.dumps(correlation), 'Bearer wrong')
    with pytest.raises(HTTPException) as classify_error:
        ns['classify'](image_upload(), json.dumps(correlation), 'Bearer wrong')
    assert detect_error.value.status_code == classify_error.value.status_code == 401
    assert lock.entries == 0

    detection = ns['detect'](image_upload(), json.dumps(correlation), 'Bearer secret-key')
    classification = ns['classify'](image_upload(), json.dumps(correlation), 'Bearer secret-key')
    assert detection['result']['objects'] == []
    assert classification['result']['candidate'] == 'servo_motor'
    assert lock.entries == 2


def test_correlation_is_echoed():
    class Models:
        def locate(self, image):
            return {'image_size': list(image.size), 'objects': [], 'raw_detections': 0,
                    'detection_seconds': 0.0}

        def classify_crop(self, image):
            return {'candidate': None, 'recognized': False}

    correlation = {'generation': 3, 'frame_id': 'f-18', 'object_id': 1}
    ns = {'API_KEY': 'secret-key', 'model_lock': threading.Lock(), 'multi_recognizer': Models(),
          'vlm_model': object(), 'HTTPException': HTTPException, 'secrets': __import__('secrets'),
          'json': json, 'io': io, 'Image': Image, 'threading': threading,
          'ImageOps': __import__('PIL.ImageOps', fromlist=['ImageOps'])}
    install_notebook_functions(ns, '_authorize', '_read_image', '_parse_correlation',
                               '_process_scan', 'detect', 'classify')

    for operation in (ns['detect'], ns['classify']):
        response = operation(image_upload(), json.dumps(correlation), 'Bearer secret-key')
        assert response['correlation'] == correlation
        assert 'result' in response


def test_invalid_detect_contract_rejected():
    with Image.open(io.BytesIO(jpeg_bytes())) as image:
        size = image.size
    malformed = {'image_size': list(size), 'objects': [
        {'id': 1, 'box': [0, 0, size[0] + 1, size[1]],
         'detector_label': 'tool', 'detector_score': .9}],
        'raw_detections': 1, 'detection_seconds': .1}
    validator = getattr(recognizer_module, 'validate_detections', None)
    assert callable(validator), 'detection validation must be available before using DINO results'
    with pytest.raises(RecognizerError):
        validator(malformed, size)


def test_classification_acceptance_contract_is_coherent():
    valid = {'candidate': 'servo_motor', 'recognized': True, 'similarity': .95,
             'margin': .8, 'reason': 'accepted', 'ranking': [
                 {'object': 'servo_motor', 'similarity': .95},
                 {'object': 'solder', 'similarity': .15}]}
    validator = getattr(recognizer_module, 'validate_classification', None)
    assert callable(validator), 'classification validation must be available before using Qwen results'
    assert validator(valid) is valid
    invalid = {**valid, 'candidate': None}
    with pytest.raises(RecognizerError):
        validator(invalid)


def test_gradio_scan_not_supported(cfg):
    client = GradioRecognizer(cfg)
    assert callable(getattr(client, 'detect', None)), 'recognizers must expose split detection'
    assert callable(getattr(client, 'classify', None)), 'recognizers must expose split classification'
    for method in (client.detect, client.classify):
        with pytest.raises(RecognizerError, match='manual|Gradio|REST|escaneo'):
            asyncio.run(method(jpeg_bytes(), {'frame_id': 1}))


def test_mock_scan_operations_need_no_models_and_echo_correlation(cfg, monkeypatch):
    monkeypatch.setattr(MockRecognizer, 'force', 'digital_multimeter,servo_motor')
    client = MockRecognizer(cfg)
    correlation = {'generation': 7, 'object_id': 2}

    async def exercise():
        detections = await client.detect(jpeg_bytes(), correlation)
        classification = await client.classify(jpeg_bytes(), correlation)
        return detections, classification

    detections, classification = asyncio.run(exercise())
    assert detections['correlation'] == classification['correlation'] == correlation
    assert detections['result']['objects'][1]['detector_label'] == 'servo_motor'
    assert classification['result']['candidate'] == 'servo_motor'
    assert classification['result']['recognized'] is True


def test_rest_split_operations_echo_and_derive_urls_from_predict(cfg):
    seen = []

    async def handler(request):
        await request.aread()
        seen.append((request.url.path, request.headers.get('content-type', ''), request.content))
        correlation = {'generation': 1, 'frame_id': request.url.path}
        return httpx.Response(200, json={'correlation': correlation, 'result': {'ok': True}})

    recognizer = RestRecognizer(cfg)
    old = recognizer.client
    asyncio.run(old.aclose())
    recognizer.set_url('https://models.example/predict')
    recognizer.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def exercise():
        correlation = {'generation': 1, 'frame_id': 'f-1'}
        assert callable(getattr(recognizer, 'detect', None)), 'REST recognizer must expose /detect'
        assert callable(getattr(recognizer, 'classify', None)), 'REST recognizer must expose /classify'
        detected = await recognizer.detect(jpeg_bytes(), correlation)
        classified = await recognizer.classify(jpeg_bytes(), correlation)
        await recognizer.close()
        return detected, classified

    detected, classified = asyncio.run(exercise())
    assert detected['correlation'] == {'generation': 1, 'frame_id': '/detect'}
    assert classified['correlation'] == {'generation': 1, 'frame_id': '/classify'}
    assert [item[0] for item in seen] == ['/detect', '/classify']
    assert all('multipart/form-data' in item[1] and b'correlation' in item[2]
               and b'frame_id' in item[2] for item in seen)

