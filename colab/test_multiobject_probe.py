"""Tests of crop routing; they do not validate model accuracy."""
from PIL import Image
from kiwi_multiobject import MultiObjectProbe, select_detections


def test_deduplicate_and_clamp():
    items = [{'box': [-3, -2, 40, 40], 'score': .8, 'label': 'meter'},
             {'box': [0, 0, 40, 40], 'score': .7, 'label': 'instrument'},
             {'box': [60, 60, 110, 110], 'score': .6, 'label': 'servo'},
             {'box': [3, 3, 3, 7], 'score': .5, 'label': 'empty'}]
    selected = select_detections(items, (100, 100))
    assert [x['box'] for x in selected] == [[0, 0, 40, 40], [60, 60, 100, 100]]


def test_each_crop_is_classified_and_rejection_preserved():
    probe = MultiObjectProbe.__new__(MultiObjectProbe)
    probe.detect = lambda image, threshold: ([
        {'box': [0, 0, 20, 20], 'score': .8, 'label': 'tool'},
        {'box': [30, 30, 50, 50], 'score': .7, 'label': 'tool'}], 2)
    seen = []
    def classify(crop):
        seen.append(crop.size)
        return {'candidate': 'servo_motor', 'recognized': len(seen) == 1}
    probe.classify = classify
    annotated, gallery, result = probe.identify(Image.new('RGB', (60, 60)))
    assert len(seen) == len(gallery) == len(result['objects']) == 2
    assert [o['recognized'] for o in result['objects']] == [True, False]
    assert annotated.size == (60, 60)


def test_empty_detection_does_not_call_qwen():
    probe = MultiObjectProbe.__new__(MultiObjectProbe)
    probe.detect = lambda image, threshold: ([], 0)
    probe.classify = lambda crop: (_ for _ in ()).throw(AssertionError('No crop'))
    assert probe.identify(Image.new('RGB', (60, 60)))[2]['objects'] == []
