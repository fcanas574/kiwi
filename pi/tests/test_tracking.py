from kiwi_pi.tracking import Tracker


RULES = {
    'association_iou': 0.3,
    'ambiguity_delta': 0.1,
    'stable_samples': 3,
    'stable_seconds': 1,
    'stable_iou': 0.6,
    'area_tolerance': 0.15,
    'center_tolerance': 0.02,
    'missing_samples': 3,
    'max_gap_seconds': 10,
    'retry_seconds': 5,
    'rearm_samples': 3,
}
SIZE = (960, 720)
STEADY_BOX = [120, 90, 360, 450]


def detection(box=STEADY_BOX, *, label='tool', object_id=1):
    return {'id': object_id, 'box': list(box), 'detector_label': label,
            'detector_score': 0.9}


def recognition(category='servo_motor', *, accepted=True):
    return {'candidate': category if accepted else None,
            'recognized': accepted,
            'reason': 'fixture',
            'similarity': 0.95 if accepted else None,
            'margin': 0.8 if accepted else None,
            'ranking': [{'object': category, 'similarity': 0.95}] if accepted else []}


def stable_track(tracker, box=STEADY_BOX, *, label='tool'):
    for frame_id, now in ((1, 0), (2, 0.5), (3, 1)):
        tracker.update(frame_id, SIZE, [detection(box, label=label)], now)


def test_three_compatible_samples_are_required_and_reservation_is_unique():
    tracker = Tracker(RULES)
    tracker.update(1, SIZE, [detection()], 0)
    assert tracker.next_candidate(0) is None

    tracker.update(2, SIZE, [detection()], 0.5)
    assert tracker.next_candidate(0.5) is None

    tracker.update(3, SIZE, [detection()], 1)
    candidate = tracker.next_candidate(1)
    assert candidate is not None
    assert set(candidate) == {'track_id', 'revision', 'frame_id', 'box'}
    assert candidate['frame_id'] == 3
    assert candidate['box'] == STEADY_BOX
    assert tracker.next_candidate(1) is None


def test_movement_restarts_stability():
    tracker = Tracker(RULES)
    moved = [150, 90, 390, 450]
    tracker.update(1, SIZE, [detection()], 0)
    tracker.update(2, SIZE, [detection()], 0.5)
    tracker.update(3, SIZE, [detection(moved)], 1)
    assert tracker.next_candidate(1) is None
    tracker.update(4, SIZE, [detection(moved)], 1.5)
    assert tracker.next_candidate(1.5) is None
    tracker.update(5, SIZE, [detection(moved)], 2)
    assert tracker.next_candidate(2) is not None


def test_scale_change_restarts_stability():
    tracker = Tracker(RULES)
    larger = [120, 90, 384, 486]
    tracker.update(1, SIZE, [detection()], 0)
    tracker.update(2, SIZE, [detection()], 0.5)
    tracker.update(3, SIZE, [detection(larger)], 1)
    assert tracker.next_candidate(1) is None
    tracker.update(4, SIZE, [detection(larger)], 1.5)
    assert tracker.next_candidate(1.5) is None
    tracker.update(5, SIZE, [detection(larger)], 2)
    assert tracker.next_candidate(2) is not None


def test_resolution_change_preserves_normalized_geometry_and_returns_current_box():
    tracker = Tracker(RULES)
    tracker.update(1, (960, 720), [detection([96, 72, 288, 360])], 0)
    tracker.update(2, (1920, 1440), [detection([192, 144, 576, 720])], 0.5)
    assert tracker.next_candidate(0.5) is None
    tracker.update(3, (1920, 1440), [detection([192, 144, 576, 720])], 1)
    candidate = tracker.next_candidate(1)
    assert candidate is not None
    assert candidate['box'] == [192, 144, 576, 720]


def test_qwen_needs_later_detection():
    tracker = Tracker(RULES)
    stable_track(tracker)
    candidate = tracker.next_candidate(1)
    assert candidate is not None

    accepted = recognition()
    tracker.set_provisional(candidate['track_id'], candidate['revision'], accepted, 1.1)
    assert tracker.category_visibility() == {}

    events = tracker.update(4, SIZE, [detection()], 1.5)
    assert len(events) == 1
    assert events[0] == {
        'track_id': candidate['track_id'],
        'revision': candidate['revision'],
        'box': STEADY_BOX,
        'recognition': accepted,
        'frame_id': 4,
    }
    assert tracker.category_visibility() == {'servo_motor': True}


def test_changed_tool_discards_provisional():
    tracker = Tracker(RULES)
    stable_track(tracker)
    candidate = tracker.next_candidate(1)
    assert candidate is not None
    tracker.set_provisional(candidate['track_id'], candidate['revision'], recognition(), 1.1)

    shifted = [150, 90, 390, 450]  # Still associates by IoU, but is not the classified geometry.
    events = tracker.update(4, SIZE, [detection(shifted)], 1.5)
    assert events == []
    assert tracker.category_visibility() == {}
    current = tracker.snapshot()[0]
    assert current['state'] != 'accepted'
    assert current['revision'] > candidate['revision']


def test_two_missing_samples_preserve_track():
    tracker = Tracker(RULES)
    stable_track(tracker)
    track_id = tracker.snapshot()[0]['track_id']
    tracker.update(4, SIZE, [], 1.5)
    tracker.update(5, SIZE, [], 2)
    assert [item['track_id'] for item in tracker.snapshot()] == [track_id]
    tracker.update(6, SIZE, [detection()], 2.5)
    assert [item['track_id'] for item in tracker.snapshot()] == [track_id]
    assert tracker.next_candidate(2.5) is None


def test_third_missing_sample_expires_track():
    tracker = Tracker(RULES)
    tracker.update(1, SIZE, [detection()], 0)
    for frame_id, now in ((2, 0.5), (3, 1), (4, 1.5)):
        tracker.update(frame_id, SIZE, [], now)
    assert tracker.snapshot() == []


def test_tracker_keeps_at_most_twelve_tracks():
    tracker = Tracker(RULES)
    boxes = [[i * 70, 20, i * 70 + 40, 60] for i in range(13)]
    tracker.update(1, (1000, 100),
                   [detection(box, object_id=i + 1) for i, box in enumerate(boxes)], 0)
    assert len(tracker.snapshot()) == 12


def test_gap_over_ten_seconds_resets_stability():
    tracker = Tracker(RULES)
    tracker.update(1, SIZE, [detection()], 0)
    tracker.update(2, SIZE, [detection()], 0.5)
    tracker.update(3, SIZE, [detection()], 11)
    tracker.update(4, SIZE, [detection()], 11.5)
    assert tracker.next_candidate(11.5) is None
    tracker.update(5, SIZE, [detection()], 12)
    assert tracker.next_candidate(12) is not None


def test_accepted_track_not_reclassified():
    tracker = Tracker(RULES)
    stable_track(tracker)
    candidate = tracker.next_candidate(1)
    assert candidate is not None
    tracker.set_provisional(candidate['track_id'], candidate['revision'], recognition(), 1)
    assert tracker.update(4, SIZE, [detection()], 1.5)

    for frame_id, now in ((5, 2), (6, 2.5), (7, 3)):
        tracker.update(frame_id, SIZE, [detection()], now)
        assert tracker.next_candidate(now) is None


def test_rejection_requires_change_and_cooldown():
    tracker = Tracker(RULES)
    stable_track(tracker)
    candidate = tracker.next_candidate(1)
    assert candidate is not None
    tracker.set_provisional(candidate['track_id'], candidate['revision'],
                            recognition(accepted=False), 1)
    events = tracker.update(4, SIZE, [detection()], 1.5)
    assert len(events) == 1
    assert events[0]['recognition']['recognized'] is False

    for frame_id, now in ((5, 6), (6, 6.5), (7, 7)):
        tracker.update(frame_id, SIZE, [detection()], now)
        assert tracker.next_candidate(now) is None

    moved = [150, 90, 390, 450]
    tracker.update(8, SIZE, [detection(moved)], 7.5)
    assert tracker.next_candidate(7.5) is None
    tracker.update(9, SIZE, [detection(moved)], 8)
    assert tracker.next_candidate(8) is None
    tracker.update(10, SIZE, [detection(moved)], 8.5)
    assert tracker.next_candidate(8.5) is not None


def test_candidates_are_fair():
    tracker = Tracker(RULES)
    boxes = [[40, 80, 240, 380], [560, 80, 760, 380]]
    for frame_id, now in ((1, 0), (2, 0.5), (3, 1)):
        detections = [detection(box, object_id=i) for i, box in enumerate(boxes, 1)]
        tracker.update(frame_id, SIZE, detections, now)

    first = tracker.next_candidate(1)
    second = tracker.next_candidate(1)
    assert first is not None and second is not None
    assert first['track_id'] != second['track_id']
    assert first['track_id'] < second['track_id']
    assert tracker.next_candidate(1) is None


def test_uncertain_association_does_not_declare_known_category_absent():
    tracker = Tracker(RULES)
    boxes = [[10, 10, 60, 60], [35, 10, 85, 60]]
    for frame_id, now in ((1, 0), (2, 0.5), (3, 1)):
        tracker.update(frame_id, SIZE,
                       [detection(box, object_id=i) for i, box in enumerate(boxes, 1)], now)
    candidate = tracker.next_candidate(1)
    assert candidate is not None
    tracker.set_provisional(candidate['track_id'], candidate['revision'], recognition(), 1)
    events = tracker.update(4, SIZE,
                            [detection(box, object_id=i) for i, box in enumerate(boxes, 1)], 1.5)
    assert len(events) == 1
    assert tracker.category_visibility() == {'servo_motor': True}

    midpoint = [22, 10, 72, 60]
    tracker.update(5, SIZE, [detection(midpoint)], 2)
    assert tracker.category_visibility() == {'servo_motor': None}


def test_ambiguous_sample_invalidates_inflight_classification():
    tracker = Tracker(RULES)
    box = [100, 100, 300, 400]
    for frame_id, now in ((1, 0), (2, 0.5), (3, 1)):
        tracker.update(frame_id, SIZE, [detection(box)], now)
    candidate = tracker.next_candidate(1)
    assert candidate is not None

    near_duplicate = [105, 100, 305, 400]
    tracker.update(4, SIZE, [detection(box), detection(near_duplicate, object_id=2)], 1.5)
    tracker.set_provisional(candidate['track_id'], candidate['revision'], recognition(), 1.6)
    assert tracker.category_visibility() == {}
    assert tracker.snapshot()[0]['revision'] > candidate['revision']
    assert tracker.snapshot()[0]['state'] != 'accepted'


def test_classification_failure_waits_for_new_sample_and_cooldown():
    tracker = Tracker(RULES)
    stable_track(tracker)
    candidate = tracker.next_candidate(1)
    assert candidate is not None
    tracker.classification_failed(candidate['track_id'], candidate['revision'], 1)
    assert tracker.next_candidate(6) is None

    tracker.update(4, SIZE, [detection()], 6)
    assert tracker.next_candidate(6) is not None


def test_reset_clears_tracks_and_known_categories():
    tracker = Tracker(RULES)
    stable_track(tracker)
    candidate = tracker.next_candidate(1)
    tracker.set_provisional(candidate['track_id'], candidate['revision'], recognition(), 1)
    tracker.update(4, SIZE, [detection()], 1.5)
    assert tracker.category_visibility() == {'servo_motor': True}

    tracker.reset()
    assert tracker.snapshot() == []
    assert tracker.category_visibility() == {}


def test_known_category_is_absent_only_after_a_valid_empty_sample():
    tracker = Tracker(RULES)
    stable_track(tracker)
    candidate = tracker.next_candidate(1)
    tracker.set_provisional(candidate['track_id'], candidate['revision'], recognition(), 1)
    tracker.update(4, SIZE, [detection()], 1.5)
    assert tracker.category_visibility() == {'servo_motor': True}

    tracker.update(5, SIZE, [], 2)
    assert tracker.category_visibility() == {'servo_motor': False}
