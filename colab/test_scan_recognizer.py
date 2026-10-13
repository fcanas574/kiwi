"""The split DINO/Qwen surface keeps model work independently callable."""
from PIL import Image

from kiwi_multiobject import MultiObjectRecognizer


def probe_with(classify):
    probe = MultiObjectRecognizer.__new__(MultiObjectRecognizer)
    probe.classify = classify
    return probe


def test_locate_does_not_call_qwen():
    calls = {'qwen': 0, 'detector': 0, 'size': None, 'mode': None}

    def classify(image):
        calls['qwen'] += 1
        raise AssertionError('locate must not classify crops')

    probe = probe_with(classify)

    def detect(image, threshold):
        calls['detector'] += 1
        calls['size'], calls['mode'] = image.size, image.mode
        return ([{'box': [0, 0, 5, 5], 'score': .8, 'label': 'tool'}] * 20, 20)

    probe.detect = detect
    source = Image.new('RGB', (80, 40))
    exif = Image.Exif()
    exif[274] = 6
    source.info['exif'] = exif.tobytes()

    assert callable(getattr(probe, 'locate', None)), 'locate must expose DINO alone'
    result = probe.locate(source)

    assert calls == {'qwen': 0, 'detector': 1, 'size': (40, 80), 'mode': 'RGB'}
    assert result['image_size'] == [40, 80]
    assert len(result['objects']) <= 12
    assert result['objects'][0] == {'id': 1, 'box': [0, 0, 5, 5],
                                    'detector_label': 'tool', 'detector_score': .8}
    assert result['raw_detections'] == 20
    assert result['detection_seconds'] >= 0


def test_classify_does_not_call_dino():
    calls = {'qwen': 0, 'detector': 0}
    expected = {'candidate': 'servo_motor', 'recognized': True, 'similarity': .95,
                'margin': .8, 'reason': 'fixture', 'ranking': []}

    def classify(image):
        calls['qwen'] += 1
        return expected

    probe = probe_with(classify)
    probe.detect = lambda *args, **kwargs: calls.__setitem__('detector', calls['detector'] + 1)

    assert callable(getattr(probe, 'classify_crop', None)), 'classify_crop must expose Qwen alone'
    assert probe.classify_crop(Image.new('RGB', (12, 9))) is expected
    assert calls == {'qwen': 1, 'detector': 0}


def test_manual_identify_contract_unchanged():
    probe = probe_with(lambda crop: {'candidate': 'servo_motor', 'recognized': True,
                                    'similarity': .95, 'margin': .8, 'reason': 'fixture',
                                    'ranking': []})
    probe.detect = lambda image, threshold: ([
        {'box': [5, 5, 25, 25], 'score': .8, 'label': 'micro servo'},
    ], 1)

    annotated, gallery, response = probe.identify(Image.new('RGB', (40, 40)))

    assert annotated.size == (40, 40)
    assert len(gallery) == 1 and gallery[0][0].size == (24, 24)
    assert response['mode'] == 'grounding_dino_qwen'
    assert response['image_size'] == [40, 40]
    assert response['objects'][0]['category'] == 'servo_motor'
    assert response['objects'][0]['recognized'] is True
    assert response['objects'][0]['recognition']['candidate'] == 'servo_motor'
    assert response['detection_seconds'] >= 0
    assert response['elapsed_seconds'] >= response['detection_seconds']

