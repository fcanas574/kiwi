import ast
import json
import threading
from pathlib import Path

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
                return compile(ast.Module(body=[node], type_ignores=[]), str(NOTEBOOK), 'exec')
    raise AssertionError(f'Falta la función {name} en el notebook')


def test_health_reports_primary_multiobject_models():
    ns = {'multi_recognizer': object(), 'vlm_model': object(), 'names': ['servo_motor']}
    exec(notebook_function('health'), ns)
    data = ns['health']()
    assert data['ok'] is True
    assert data['mode'] == 'grounding_dino_qwen'
    assert data['calibrated'] is False
    assert data['capabilities'] == {'predict': True, 'detect': True, 'classify': True}


def test_missing_detector_does_not_fall_back_to_single_object():
    ns = {'model_lock': threading.Lock(), 'identify': lambda image: 'single'}
    exec(notebook_function('identify_current'), ns)
    import pytest
    with pytest.raises(RuntimeError, match='DINO'):
        ns['identify_current'](None)


def test_rest_returns_same_list_as_gradio():
    response = {'objects': [], 'image_size': [20, 10]}
    ns = {'identify_current': lambda image: ('annotated', [], response)}
    exec(notebook_function('identify_objects'), ns)
    assert ns['identify_objects'](None) is response
