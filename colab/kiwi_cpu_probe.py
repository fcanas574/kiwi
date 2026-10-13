"""CPU-only experiment. Run in a fresh Colab CPU session, after installing
transformers, accelerate and bitsandbytes. No station or background filters change.
"""
import ast
import gc
import io
import json
import os
import time
import urllib.request
from pathlib import Path

import torch
from PIL import Image, ImageDraw, ImageOps
from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

BASE = 'https://raw.githubusercontent.com/fcanas574/kiwi/'
EXPERIMENT = BASE + 'codex/grounding-dino-probe/'
OUT = Path('/content/kiwi_cpu_probe')
OUT.mkdir(exist_ok=True)
torch.set_num_threads(min(2, os.cpu_count() or 1))


def fetch_text(url):
    return urllib.request.urlopen(url).read().decode()


exec(compile(fetch_text(EXPERIMENT + 'colab/kiwi_multiobject.py'), 'kiwi_multiobject.py', 'exec'))
canvas = Image.new('RGB', (1200, 400), 'white')
for index, category in enumerate(['digital_multimeter', 'servo_motor', 'solder']):
    url = BASE + f'main/dataset_prepared/references/{category}/{category}_001.jpg'
    photo = ImageOps.exif_transpose(Image.open(io.BytesIO(urllib.request.urlopen(url).read()))).convert('RGB')
    photo.thumbnail((390, 390))
    canvas.paste(photo, (index * 400, 0))

print('CPU_STAGE: DINO; montage artificial, sin filtros nuevos de fondo', flush=True)
detector = MultiObjectProbe(lambda image: {})
detector_start = time.perf_counter()
detections, raw_count = detector.detect(canvas)
detector_seconds = round(time.perf_counter() - detector_start, 3)
del detector
gc.collect()
print('CPU_STAGE: DINO terminado', len(detections), 'recortes;', detector_seconds, 'segundos', flush=True)

# Reuse categories and the exact Qwen decision functions; only execution precision
# and image resolution differ from the GPU experiment.
notebook = json.loads(fetch_text(EXPERIMENT + 'Kiwi_Multiobject_Probe_Colab.ipynb'))
tree = ast.parse(''.join(notebook['cells'][3]['source']))
CATEGORIES = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'CATEGORIES' for t in node.targets))
LOCATION_BY_CATEGORY = {}
reference_vectors = None
VLM_CHECK_WITH_REFERENCES = False


def prepare_image(image):
    return ImageOps.exif_transpose(image).convert('RGB')


VLM_ID = 'Qwen/Qwen3-VL-4B-Instruct'
print('CPU_STAGE: cargando Qwen 4B en 4 bits; primera descarga ~9 GB', flush=True)
load_start = time.perf_counter()
vlm_processor = AutoProcessor.from_pretrained(VLM_ID, min_pixels=64 * 32 * 32, max_pixels=128 * 32 * 32)
quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                                 bnb_4bit_compute_dtype=torch.float32)
vlm_model = AutoModelForImageTextToText.from_pretrained(
    VLM_ID, device_map='cpu', dtype=torch.float32, quantization_config=quantization,
    low_cpu_mem_usage=True, attn_implementation='sdpa').eval()
load_seconds = round(time.perf_counter() - load_start, 3)
print('CPU_STAGE: Qwen cargado;', load_seconds, 'segundos;',
      round(vlm_model.get_memory_footprint() / 1e9, 2), 'GB de pesos', flush=True)
assignments = {'VLM_MIN_PROBABILITY', 'VLM_MIN_MARGIN', 'OTHER', 'VLM_DESCRIPTIONS',
               'VLM_OPTIONS', 'VLM_LETTERS', 'VLM_LETTER_IDS'}
functions = {'vlm_prompt', 'vlm_scores', 'identify_vlm'}
nodes = [node for node in tree.body if
         (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in assignments for t in node.targets))
         or (isinstance(node, ast.FunctionDef) and node.name in functions)]
exec(compile(ast.Module(body=nodes, type_ignores=[]), 'kiwi_vlm_reused.py', 'exec'))

report = {'environment': 'Colab CPU', 'model': VLM_ID, 'quantization': 'bitsandbytes NF4',
          'compute_dtype': 'float32', 'image_max_pixels': 128 * 32 * 32,
          'sample': 'artificial montage of three reference photos, not independent evaluation',
          'reference_verification': False, 'detection_seconds': detector_seconds,
          'raw_detections': raw_count, 'qwen_load_seconds': load_seconds, 'objects': []}
annotation = canvas.copy()
draw = ImageDraw.Draw(annotation)
start = time.perf_counter()
for index, detection in enumerate(detections, 1):
    box = detection['box']
    pad = max(2, round(min(box[2] - box[0], box[3] - box[1]) * .04))
    crop_box = [max(0, box[0] - pad), max(0, box[1] - pad),
                min(canvas.width, box[2] + pad), min(canvas.height, box[3] + pad)]
    crop = canvas.crop(crop_box)
    print('CPU_STAGE: clasificando', index, '/', len(detections), 'box', box, flush=True)
    result = identify_vlm(crop)
    item = {'id': index, 'box': box, 'crop_box': crop_box,
            'detector_label': detection['label'], 'detector_score': detection['score'],
            'recognition': result}
    report['objects'].append(item)
    report['qwen_inference_seconds'] = round(time.perf_counter() - start, 3)
    (OUT / 'results.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
    caption = f"{index}: {result['candidate']} ({'aceptado' if result['recognized'] else 'revisar'})"
    draw.rectangle(box, outline='green' if result['recognized'] else 'orange', width=3)
    draw.text((box[0], max(0, box[1] - 14)), caption, fill='black')
    crop.save(OUT / f'crop_{index:02d}.jpg')
    print('CPU_RESULT', json.dumps(item, ensure_ascii=False), flush=True)

annotation.save(OUT / 'annotated.jpg')
print('CPU_COMPLETE', json.dumps({'objects': len(report['objects']),
                                  'accepted': sum(x['recognition']['recognized'] for x in report['objects']),
                                  'detection_seconds': detector_seconds,
                                  'qwen_inference_seconds': report['qwen_inference_seconds']}, ensure_ascii=False), flush=True)
display(annotation)
