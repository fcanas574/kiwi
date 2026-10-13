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


def test_health_reports_active_vlm():
    vlm = lambda image: {'mode': 'vlm'}
    ns = {'identify': vlm, 'identify_vlm': vlm, 'reference_vectors': [], 'names': ['servo_motor'],
          'MIN_SIMILARITY': None, 'MIN_MARGIN': None,
          'VLM_MIN_PROBABILITY': .9, 'VLM_MIN_MARGIN': .5}
    exec(notebook_function('health'), ns)
    assert ns['health']()['mode'] == 'vlm'


def test_gradio_resolves_current_recognizer():
    ns = {'identify': lambda image: 'embeddings', 'model_lock': threading.Lock()}
    exec(notebook_function('identify_current'), ns)
    assert ns['identify_current'](None) == 'embeddings'
    ns['identify'] = lambda image: 'vlm'
    assert ns['identify_current'](None) == 'vlm'
