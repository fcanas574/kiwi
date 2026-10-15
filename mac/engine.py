"""Native Mac recognition: DINO localization and Qwen option-token scores.

Scores are relative probabilities among configured options, not calibrated
real-world accuracy. The Raspberry retains temporal confirmation and GPIO.
"""
from __future__ import annotations

import ast
from pathlib import Path
import inspect
import time

from PIL import Image, ImageOps

REPO = Path(__file__).resolve().parents[1]
QWEN = 'mlx-community/Qwen3-VL-4B-Instruct-4bit'
DINO = 'IDEA-Research/grounding-dino-tiny'


def literal_assignment(path, name):
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(name)


CATEGORIES = literal_assignment(REPO / 'dataset_prepared/colab_categories.py', 'CATEGORIES')
DESCRIPTIONS = literal_assignment(REPO / 'colab/kiwi_vlm.py', 'VLM_DESCRIPTIONS')
OPTIONS = [name for name in CATEGORIES if name != 'otros'] + ['otros']
LETTERS = [chr(65 + i) for i in range(len(OPTIONS))]
LOCATIONS = dict(zip(('digital_multimeter', 'oscilloscope', 'function_generator',
                      'bench_power_supply', 'soldering_station', 'solder',
                      'servo_motor', 'variable_capacitor', 'power_adapter'), range(1, 10)))
DETECTION_PROMPT = ('a soldering station. a digital multimeter. a laboratory power supply. '
                    'a servo motor. an oscilloscope. a function generator. a variable capacitor. '
                    'a capacitor. a power adapter. a yellow multimeter box. a soldering iron.')


def prompt():
    lines = ['You are checking which tool someone is returning to an electronics lab shelf.',
             'Look only at the main object in the photo; ignore hands, tables, cables and clutter.',
             'Decide by the front panel. If only the back, bottom or side is visible and the front '
             'panel cannot be seen, choose the last option.', 'Options:']
    for letter, name in zip(LETTERS, OPTIONS):
        description = (DESCRIPTIONS.get(name, CATEGORIES[name]) if name != 'otros' else
                       'None of the above or cannot tell: unknown object, resistor, LED, wire, '
                       'circuit board, pliers, screwdriver, phone, no clear object or only the back/side.')
        lines.append(f'{letter}. {description}.')
    lines.append('Answer with only the letter of the correct option.')
    return '\n'.join(lines)


def decision(probabilities):
    ranking = sorted(zip(OPTIONS, probabilities), key=lambda item: item[1], reverse=True)
    name, score = ranking[0]
    margin = score - ranking[1][1]
    accepted = name in LOCATIONS and score >= .90 and margin >= .50
    return {'candidate': name, 'recognized': accepted, 'similarity': score, 'margin': margin,
            'location_id': LOCATIONS.get(name) if accepted else None,
            'reason': 'Reconocido por Qwen local.' if accepted else 'Objeto desconocido, ambiguo o sin posición.',
            'ranking': [{'object': n, 'similarity': p} for n, p in ranking],
            'model': QWEN, 'mode': 'mlx_qwen'}


def square_image(image, side=448):
    # Equal token lengths make batches independent without padded text attention.
    image = ImageOps.exif_transpose(image).convert('RGB')
    return ImageOps.pad(image, (side, side), color=(255, 255, 255), method=Image.Resampling.LANCZOS)


class Engine:
    def __init__(self, detector_device='mps', side=384):
        import mlx.core as mx
        from mlx_vlm import load
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config
        import torch
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        # Bound unused buffers and keep the small model resident on this 16 GB
        # Mac, so idle periods do not cause slow first requests from paging.
        mx.set_cache_limit(256 * 1024 ** 2)
        mx.set_memory_limit(6 * 1024 ** 3)
        recommended = mx.device_info()['max_recommended_working_set_size']
        mx.set_wired_limit(min(4 * 1024 ** 3, recommended // 2))
        self.side = side
        print('Cargando Qwen en MLX…', flush=True)
        self.model, self.processor = load(QWEN)
        config = load_config(QWEN)
        self.formatted_prompt = apply_chat_template(self.processor, config, prompt(), num_images=1)
        self.letter_ids = [self.processor.tokenizer.encode(letter, add_special_tokens=False)[0] for letter in LETTERS]
        if len(set(self.letter_ids)) != len(LETTERS):
            raise ValueError('Las letras de las opciones deben tener tokens distintos.')
        image_processor = self.processor.image_processor
        image_processor.size = {'shortest_edge': side * side, 'longest_edge': side * side}
        image_processor.min_pixels = image_processor.max_pixels = side * side
        self.detector_device = detector_device
        if detector_device == 'mps' and not torch.backends.mps.is_available():
            raise RuntimeError('Se requiere Apple Silicon con MPS para esta prueba.')
        print('Cargando Grounding DINO en ' + detector_device + '…', flush=True)
        self.detector_processor = AutoProcessor.from_pretrained(DINO)
        self.detector_processor.image_processor.size = {'shortest_edge': 640, 'longest_edge': 960}
        self.detector = AutoModelForZeroShotObjectDetection.from_pretrained(DINO).to(detector_device).eval()
        mx.eval(self.model.parameters())
        self.last_classification_seconds = 0.0

    def classify_many(self, images, reference_forward=False):
        import mlx.core as mx
        from mlx_vlm.utils import prepare_inputs
        if not 1 <= len(images) <= 3:
            raise ValueError('La clasificación por lote admite de una a tres imágenes.')
        started = time.perf_counter()
        side = 448 if len(images) == 1 else self.side
        self.processor.image_processor.size = {'shortest_edge': side * side, 'longest_edge': side * side}
        self.processor.image_processor.min_pixels = self.processor.image_processor.max_pixels = side * side
        images = [square_image(image, side) for image in images]
        inputs = prepare_inputs(self.processor, images=images,
                                prompts=[self.formatted_prompt] * len(images), padding=True)
        ids = inputs.pop('input_ids')
        attention = inputs.pop('attention_mask', None)
        pixels = inputs.pop('pixel_values')
        if reference_forward:
            output = self.model(ids, pixel_values=pixels, mask=attention, **inputs)
            logits = output.logits[:, -1, :]
        else:
            features = self.model.get_input_embeddings(ids, pixel_values=pixels, mask=attention, **inputs)
            values = features.to_dict()
            hidden = self.model.language_model.model(ids, **{
                key: values[key] for key in ('inputs_embeds', 'position_ids', 'visual_pos_masks',
                                             'deepstack_visual_embeds') if values.get(key) is not None})
            # Project only the final token, avoiding a vocabulary matrix for
            # every image/prompt token. The transformer and vision tower stay intact.
            last = hidden[:, -1:, :]
            language = self.model.language_model
            output = (language.model.embed_tokens.as_linear(last) if language.args.tie_word_embeddings
                      else language.lm_head(last))
            logits = output[:, 0, :]
        selected = logits[:, mx.array(self.letter_ids)].astype(mx.float32)
        scores = mx.softmax(selected, axis=-1)
        mx.eval(scores)
        results = [decision(values) for values in scores.tolist()]
        self.last_classification_seconds = time.perf_counter() - started
        for item in results:
            item['elapsed_seconds'] = round(self.last_classification_seconds, 3)
        return results

    def classify(self, image):
        return self.classify_many([image])[0]

    def identify(self, image):
        """Keep the legacy manual /predict multi-object response contract."""
        began = time.perf_counter()
        located = self.locate(image)
        objects, crops = [], []
        for item in located['objects']:
            x1, y1, x2, y2 = item['box']
            pad = max(2, round(min(x2 - x1, y2 - y1) * .04))
            crop_box = [max(0, x1 - pad), max(0, y1 - pad),
                        min(image.width, x2 + pad), min(image.height, y2 + pad)]
            objects.append({**item, 'crop_box': crop_box})
            crops.append(image.crop(crop_box))
        results = []
        for offset in range(0, len(crops), 3):
            results.extend(self.classify_many(crops[offset:offset + 3]))
        for item, result in zip(objects, results):
            item.update(category=result['candidate'], recognized=result['recognized'], recognition=result)
        return {**located, 'objects': objects, 'mode': 'grounding_dino_qwen',
                'elapsed_seconds': round(time.perf_counter() - began, 3), 'limit': 12,
                'reason': 'Revisa objetos omitidos y componentes del mismo equipo.' if objects else 'Sin objetos detectados.'}

    def locate(self, image):
        import torch
        import sys
        sys.path.insert(0, str(REPO / 'colab'))
        from kiwi_multiobject import select_detections
        image = ImageOps.exif_transpose(image).convert('RGB')
        began = time.perf_counter()
        inputs = self.detector_processor(images=image, text=DETECTION_PROMPT, return_tensors='pt').to(self.detector_device)
        with torch.inference_mode():
            output = self.detector(**inputs)
        postprocess = self.detector_processor.post_process_grounded_object_detection
        key = 'threshold' if 'threshold' in inspect.signature(postprocess).parameters else 'box_threshold'
        found = postprocess(output, inputs.input_ids, **{key: .25}, text_threshold=.20,
                            target_sizes=[image.size[::-1]])[0]
        raw = [{'box': box.tolist(), 'score': float(score), 'label': str(label)}
               for box, score, label in zip(found['boxes'], found['scores'], found.get('text_labels', found.get('labels')))]
        selected = select_detections(raw, image.size, limit=12, iou_threshold=.45)
        # Suppress part boxes contained in the same instrument, as on the Pi.
        kept = []
        for item in selected:
            a = item['box']
            area = (a[2] - a[0]) * (a[3] - a[1])
            duplicate = False
            for old in kept:
                b = old['box']
                other = (b[2] - b[0]) * (b[3] - b[1])
                intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
                if intersection / min(area, other) >= .9:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(item)
        return {'image_size': list(image.size), 'objects': [
            {'id': i, 'box': item['box'], 'detector_label': item['label'], 'detector_score': item['score']}
            for i, item in enumerate(kept, 1)], 'raw_detections': len(raw),
            'detection_seconds': round(time.perf_counter() - began, 3)}
