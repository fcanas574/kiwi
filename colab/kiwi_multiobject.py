"""Reconocimiento principal: Grounding DINO localiza y Qwen clasifica recortes.

No sustituye identify ni modifica la API o las luces de la estación.
"""
import inspect
import math
import time
from PIL import ImageDraw, ImageOps

DETECTOR_ID = 'IDEA-Research/grounding-dino-tiny'
DETECTION_PROMPT = (
    'a soldering station. a digital multimeter. a laboratory power supply. '
    'a servo motor. an oscilloscope. a function generator. a variable capacitor. '
    'a capacitor. a power adapter. a yellow multimeter box. a soldering iron.'
)


def intersection_over_union(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    areas = [(x[2] - x[0]) * (x[3] - x[1]) for x in (a, b)]
    union = sum(areas) - intersection
    return intersection / union if union > 0 else 0


def select_detections(detections, size, limit=12, iou_threshold=.6):
    """Clamp boxes and remove near duplicates across detector labels."""
    width, height = size
    selected = []
    for item in sorted(detections, key=lambda x: x['score'], reverse=True):
        if not all(math.isfinite(v) for v in [item['score'], *item['box']]):
            continue
        x1, y1, x2, y2 = item['box']
        box = [max(0, min(width, math.floor(x1))), max(0, min(height, math.floor(y1))),
               max(0, min(width, math.ceil(x2))), max(0, min(height, math.ceil(y2)))]
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        if any(intersection_over_union(box, old['box']) > iou_threshold for old in selected):
            continue
        selected.append(dict(item, box=box))
        if len(selected) >= limit:
            break
    return selected


class MultiObjectRecognizer:
    def __init__(self, classify):
        import torch
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        self.classify = classify
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.processor = AutoProcessor.from_pretrained(DETECTOR_ID)
        self.detector = AutoModelForZeroShotObjectDetection.from_pretrained(DETECTOR_ID).to(self.device).eval()

    def detect(self, image, threshold=.25, text_threshold=.2, limit=12):
        import torch
        inputs = self.processor(images=image, text=DETECTION_PROMPT, return_tensors='pt').to(self.device)
        with torch.inference_mode():
            outputs = self.detector(**inputs)
        postprocess = self.processor.post_process_grounded_object_detection
        # Transformers versions use either threshold or box_threshold.
        threshold_name = 'threshold' if 'threshold' in inspect.signature(postprocess).parameters else 'box_threshold'
        result = postprocess(outputs, inputs.input_ids, **{threshold_name: float(threshold)},
                             text_threshold=float(text_threshold), target_sizes=[image.size[::-1]])[0]
        labels = result.get('text_labels', result.get('labels'))
        raw = [{'box': box.tolist(), 'score': float(score), 'label': str(label)}
               for box, score, label in zip(result['boxes'], result['scores'], labels)]
        return select_detections(raw, image.size, limit), len(raw)

    def identify(self, image, threshold=.25):
        if image is None:
            return None, [], {'mode': 'grounding_dino_qwen', 'image_size': None, 'objects': [], 'reason': 'Sube una foto.'}
        image = ImageOps.exif_transpose(image).convert('RGB')
        start = time.perf_counter()
        detections, raw_count = self.detect(image, threshold)
        detection_seconds = time.perf_counter() - start
        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        objects, crops = [], []
        for index, detection in enumerate(detections, 1):
            box = detection['box']
            # A small border preserves context without including most neighbors.
            pad = max(2, round(min(box[2] - box[0], box[3] - box[1]) * .04))
            crop_box = [max(0, box[0] - pad), max(0, box[1] - pad),
                        min(image.width, box[2] + pad), min(image.height, box[3] + pad)]
            crop = image.crop(crop_box)
            result = self.classify(crop)
            accepted = bool(result.get('recognized'))
            candidate = result.get('candidate')
            caption = f'{index}: {candidate or "desconocido"}' + ('' if accepted else ' (revisar)')
            color = 'green' if accepted else 'orange'
            draw.rectangle(box, outline=color, width=max(2, image.width // 400))
            draw.text((box[0], max(0, box[1] - 14)), caption, fill=color)
            crops.append((crop, caption))
            objects.append({'id': index, 'box': box, 'crop_box': crop_box,
                            'detector_label': detection['label'], 'detector_score': detection['score'],
                            'category': candidate, 'recognized': accepted, 'recognition': result})
        return annotated, crops, {'mode': 'grounding_dino_qwen', 'image_size': list(image.size), 'objects': objects, 'raw_detections': raw_count,
                                  'detection_seconds': round(detection_seconds, 3),
                                  'elapsed_seconds': round(time.perf_counter() - start, 3),
                                  'limit': 12, 'reason': 'Sin objetos detectados.' if not objects else
                                  'Revisa objetos omitidos y componentes del mismo equipo.'}


# Alias para reproducir los experimentos CPU históricos.
MultiObjectProbe = MultiObjectRecognizer
