"""Measure real model work. Reference montages are not field-accuracy tests."""
import json
from pathlib import Path
import time

from PIL import Image

from engine import Engine, REPO


def main():
    engine = Engine()
    photos = [Image.open(REPO / 'dataset_prepared/references' / path).convert('RGB') for path in (
        'digital_multimeter/digital_multimeter_001.jpg', 'servo_motor/servo_motor_001.jpg', 'solder/solder_001.jpg')]
    scene = Image.open(REPO / 'mac/results/three-reference-montage.jpg').convert('RGB')
    report = {'hardware': 'Apple M5 / 16 GB', 'sample': 'three repository reference photos and artificial montage',
              'field_validation': False, 'model': 'Qwen3-VL-4B MLX 4bit', 'runs': []}
    print('Primera clasificación (incluye calentamiento)…', flush=True)
    print(engine.classify(photos[0]), flush=True)
    print('Verificando puntuaciones contra el forward completo…', flush=True)
    reference = engine.classify_many(photos, reference_forward=True)
    optimized = engine.classify_many(photos)
    difference = max(abs(a['similarity'] - b['similarity']) for a, b in zip(reference, optimized))
    report['optimized_reference_max_score_difference'] = difference
    if difference > 0.0001 or [r['candidate'] for r in reference] != [r['candidate'] for r in optimized]:
        raise RuntimeError('El forward optimizado no coincide con la referencia.')
    print('DINO inicial…', flush=True)
    detected = engine.locate(scene)
    print(json.dumps(detected), flush=True)
    report['detections'] = detected
    for run in range(3):
        began = time.perf_counter()
        detected = engine.locate(scene)
        detection_elapsed = time.perf_counter() - began
        began = time.perf_counter()
        classified = engine.classify_many(photos)
        classification_elapsed = time.perf_counter() - began
        row = {'run': run + 1, 'detect_seconds': round(detection_elapsed, 3),
               'three_crop_seconds': round(classification_elapsed, 3),
               'detect_plus_three_crops_seconds': round(detection_elapsed + classification_elapsed, 3),
               'results': classified}
        report['runs'].append(row)
        print(json.dumps(row), flush=True)
    path = REPO / 'mac/results/local-model-benchmark.json'
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print('Resultado guardado en ' + str(path), flush=True)


if __name__ == '__main__':
    main()
