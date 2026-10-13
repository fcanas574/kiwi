# Kiwi · reconocimiento con un modelo de visión y lenguaje (VLM), sin fotos de referencia.
#
# Se ejecuta en el notebook después de las secciones 1–4 (usa CATEGORIES, decide e identify):
#     exec(open('/content/kiwi_vlm.py').read())
# Reemplaza `identify`, así que la API REST de la sección 6b y la página de Gradio pasan a usar el VLM
# sin volver a ejecutarlas. Para volver a EmbeddingGemma: identify = identify_embeddings
#
# El VLM recibe la foto y una pregunta de opción múltiple con las categorías; se leen las probabilidades
# de cada letra de respuesta. `similarity` pasa a ser esa probabilidad (0–1) y `margin` la diferencia con
# la segunda opción. La opción "ninguna" se devuelve como la categoría `otros`.
import time
import torch
from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

VLM_ID = 'Qwen/Qwen3-VL-4B-Instruct'
VLM_MIN_PROBABILITY = 0.90   # recognized=True solo por encima de esta probabilidad...
VLM_MIN_MARGIN = 0.50        # ...y con esta ventaja sobre la segunda opción
# Doble verificación: si la categoría tiene fotos de referencia (sección 5) y EmbeddingGemma elige otra
# herramienta, no se acepta. Evita LEDs equivocados con fotos ambiguas (p. ej. equipos vistos por detrás).
VLM_CHECK_WITH_REFERENCES = globals().get("VLM_CHECK_WITH_REFERENCES", False)
OTHER = 'otros'

if 'identify_embeddings' not in globals():
    identify_embeddings = identify   # el reconocimiento por fotos de referencia sigue disponible

if 'vlm_model' not in globals():
    cpu = not torch.cuda.is_available()
    print('Cargando', VLM_ID, '| CPU NF4' if cpu else '| GPU', flush=True)
    vlm_processor = AutoProcessor.from_pretrained(VLM_ID,
        min_pixels=64 * 32 * 32 if cpu else 256 * 28 * 28,
        max_pixels=128 * 32 * 32 if cpu else 640 * 28 * 28)
    kwargs = {'dtype': torch.float32 if cpu else torch.float16,
              'device_map': 'cpu' if cpu else 'auto', 'attn_implementation': 'sdpa'}
    if cpu:
        kwargs['quantization_config'] = BitsAndBytesConfig(load_in_4bit=True,
            bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.float32)
        torch.set_num_threads(2)
    vlm_model = AutoModelForImageTextToText.from_pretrained(VLM_ID, **kwargs).eval()
    print('Qwen cargado; memoria de pesos: %.1f GB' % (vlm_model.get_memory_footprint() / 1e9))

# Descripciones para el VLM: rasgos del panel frontal que distinguen equipos parecidos por fuera.
# Las categorías sin entrada aquí usan su texto de CATEGORIES.
VLM_DESCRIPTIONS = {
    'oscilloscope': 'Oscilloscope: bench instrument whose front has a large screen with a grid showing waveforms, '
                    'many knobs and BNC probe inputs labeled CH1/CH2',
    'function_generator': 'Function generator: bench instrument whose front has a numeric frequency display, '
                          'waveform buttons (sine, square, triangle) and BNC signal output connectors',
    'bench_power_supply': 'Bench DC power supply: bench instrument whose front shows voltage (V) and current (A) '
                          'readouts, adjustment knobs and red/black banana output terminals',
    'soldering_station': 'Soldering station: control unit with a temperature display or dial, connected to a '
                         'soldering iron and an iron holder stand',
    'power_adapter': 'AC/DC power adapter: small black plastic brick with an attached cable, used to power a '
                     'device from the wall outlet',
    'digital_multimeter': 'Handheld digital multimeter: portable meter with an LCD screen, a large rotary selector '
                          'dial and probe jacks (COM, V, A)',
    'solder': 'Soldering iron: pen-shaped hand tool with a plastic handle and a long pointed metal tip',
}

VLM_OPTIONS = [name for name in CATEGORIES if name != OTHER] + [OTHER]
VLM_LETTERS = [chr(ord('A') + i) for i in range(len(VLM_OPTIONS))]
VLM_LETTER_IDS = [vlm_processor.tokenizer.encode(letter, add_special_tokens=False)[0] for letter in VLM_LETTERS]
assert len(set(VLM_LETTER_IDS)) == len(VLM_LETTERS), 'Cada letra debe ser un token distinto.'


def vlm_prompt():
    lines = [
        'You are checking which tool someone is returning to an electronics lab shelf.',
        'Look only at the main object in the photo; ignore hands, tables, cables in the background and other clutter.',
        'Many bench instruments are similar boxes: decide by what is visible on their front panel. If only the back, '
        'bottom or side of an instrument is visible and you cannot see its front panel, choose the last option.',
        'Options:',
    ]
    for letter, name in zip(VLM_LETTERS, VLM_OPTIONS):
        if name == OTHER:
            lines.append(f'{letter}. None of the above or cannot tell: the main object is something else (for '
                         'example a resistor, LED, loose wire, Arduino or other circuit board, pliers, screwdriver, '
                         'phone or household item), there is no clear object, or only the back/side of an '
                         'instrument is visible.')
        else:
            lines.append(f'{letter}. {VLM_DESCRIPTIONS.get(name, CATEGORIES[name])}.')
    lines.append('Answer with only the letter of the correct option.')
    return '\n'.join(lines)


@torch.inference_mode()
def vlm_scores(image):
    messages = [{'role': 'user', 'content': [{'type': 'image', 'image': image},
                                             {'type': 'text', 'text': vlm_prompt()}]}]
    inputs = vlm_processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                               return_dict=True, return_tensors='pt').to(vlm_model.device)
    logits = vlm_model(**inputs).logits[0, -1].float()
    probabilities = torch.softmax(logits[VLM_LETTER_IDS], dim=0).tolist()
    return dict(zip(VLM_OPTIONS, probabilities))


def identify_vlm(image):
    if image is None:
        return {'recognized': False, 'candidate': None, 'location_id': None, 'reason': 'Sube una fotografía.'}
    start = time.perf_counter()
    scores = vlm_scores(prepare_image(image))
    ranking = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    candidate, probability = ranking[0]
    margin = probability - ranking[1][1]
    result = {'candidate': candidate, 'similarity': probability, 'margin': margin, 'mode': 'vlm',
              'model': VLM_ID, 'recognized': False}
    if candidate == OTHER:
        result['reason'] = 'No es una de las herramientas o no se ve su frente. Muestra el frente del objeto.'
    elif probability < VLM_MIN_PROBABILITY:
        result['reason'] = 'Confianza insuficiente: foto poco clara u objeto desconocido.'
    elif margin < VLM_MIN_MARGIN:
        result['reason'] = 'Duda entre dos herramientas. Repite la foto.'
    else:
        result.update(recognized=True, reason='Reconocido por el VLM.')
        if (VLM_CHECK_WITH_REFERENCES and globals().get('reference_vectors') is not None
                and candidate in set(reference_labels)):
            check = identify_embeddings(image)
            result['reference_candidate'] = check['candidate']
            result['reference_similarity'] = check['similarity']
            if check['candidate'] != candidate:
                result.update(recognized=False, reason='El VLM y las fotos de referencia no coinciden. '
                                                        'Muestra el frente de la herramienta.')
    result['location_id'] = LOCATION_BY_CATEGORY.get(candidate) if result['recognized'] else None
    result['elapsed_seconds'] = round(time.perf_counter() - start, 3)
    result['ranking'] = [{'object': name, 'similarity': round(p, 4)} for name, p in ranking]
    return result


print('Clasificación de recortes preparada:', VLM_ID)
