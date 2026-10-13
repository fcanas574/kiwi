import asyncio
import io
import json
from threading import Event

import pytest
from PIL import Image

from kiwi_pi.scan import ScanCoordinator, ScanError
from kiwi_pi.station import Station
from test_station import cfg as station_cfg, jpeg_bytes


@pytest.fixture
def cfg(station_cfg):
    return station_cfg


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class ScriptedRecognizer:
    name = 'fixture'
    url = 'fixture://recognizer'

    def __init__(self):
        self.detect_calls = 0
        self.classify_calls = 0
        self.detect_started = None
        self.detect_release = None
        self.classify_started = None
        self.classify_release = None
        self.fail_detect = False
        self.fail_classify = False
        self.detections = None
        self.category = 'servo_motor'
        self.recognized = True
        self.mismatch_detect_correlation = False
        self.mismatch_classify_correlation = False
        self.last_detect_size = None

    async def detect(self, jpeg, correlation):
        self.detect_calls += 1
        if self.detect_started:
            self.detect_started.set()
        if self.detect_release:
            await asyncio.to_thread(self.detect_release.wait, 2)
        if self.fail_detect:
            raise RuntimeError('fixture network failure')
        with Image.open(io.BytesIO(jpeg)) as image:
            width, height = image.size
        self.last_detect_size = (width, height)
        objects = (self.detections() if callable(self.detections) else self.detections)
        if objects is None:
            objects = [{'id': 1, 'box': [10, 10, 50, 50],
                        'detector_label': 'tool', 'detector_score': .9}]
        result = {'image_size': [width, height], 'objects': objects,
                  'raw_detections': len(objects), 'detection_seconds': .01}
        echoed = dict(correlation)
        if self.mismatch_detect_correlation:
            echoed['generation'] += 1
        return {'correlation': echoed, 'result': result}

    async def classify(self, jpeg, correlation):
        self.classify_calls += 1
        if self.classify_started:
            self.classify_started.set()
        if self.classify_release:
            await asyncio.to_thread(self.classify_release.wait, 2)
        if self.fail_classify:
            raise RuntimeError('fixture network failure')
        result = {'candidate': self.category, 'recognized': self.recognized,
                  'similarity': .95, 'margin': .8, 'reason': 'fixture',
                  'ranking': [{'object': self.category, 'similarity': .95}]}
        echoed = dict(correlation)
        if self.mismatch_classify_correlation:
            echoed['revision'] += 1
        return {'correlation': echoed, 'result': result}

    async def close(self):
        return None


def make_scan(cfg, recognizer=None, clock=None):
    cfg.raw['scan'].update(stable_samples=3, stable_seconds=1,
                           retry_seconds=0, rearm_samples=3, max_side=960)
    recognizer = recognizer or ScriptedRecognizer()
    station = Station(cfg, recognizer)
    scan = ScanCoordinator(station, recognizer, cfg, clock or Clock())
    return scan, station, recognizer


def run_case(case, cfg):
    async def run():
        scan, station, recognizer = make_scan(cfg)
        try:
            await case(scan, station, recognizer)
        finally:
            await scan.close()
            await station.stop()
    asyncio.run(run())


async def process(scan, session, sequence, jpeg=None):
    jpeg = jpeg or scan_frame_bytes()
    return await scan.process_frame(session['session_id'], session['generation'], sequence, jpeg)


def scan_frame_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (100, 100), 'red').save(buffer, 'JPEG')
    return buffer.getvalue()


def test_one_remote_inference_at_a_time(cfg):
    async def case(scan, station, recognizer):
        started, release = Event(), Event()
        recognizer.detect_started = started
        recognizer.detect_release = release
        session = scan.start('phone')
        pending = asyncio.create_task(process(scan, session, 1))
        assert await asyncio.to_thread(started.wait, 2)
        with pytest.raises(ScanError) as busy:
            await process(scan, session, 2)
        assert busy.value.status_code == 409
        assert recognizer.detect_calls == 1
        release.set()
        await pending
        assert scan.snapshot()['busy'] is False

    run_case(case, cfg)


def test_frames_are_normalized_to_nine_hundred_sixty_pixels(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        buffer = io.BytesIO()
        Image.new('RGB', (1920, 960), 'green').save(buffer, 'JPEG')
        await process(scan, session, 1, buffer.getvalue())
        assert recognizer.last_detect_size == (960, 480)

    run_case(case, cfg)


def test_stale_detection_correlation_is_rejected(cfg):
    async def case(scan, station, recognizer):
        recognizer.mismatch_detect_correlation = True
        session = scan.start('phone')
        result = await process(scan, session, 1)
        assert result['detections'] == []
        assert result['tracks'] == []
        assert result['connection_error']
        assert station.operation_id is None

    run_case(case, cfg)


def test_stale_classification_correlation_never_becomes_provisional(cfg):
    async def case(scan, station, recognizer):
        recognizer.mismatch_classify_correlation = True
        session = scan.start('phone')
        clock = scan.clock
        for sequence, now in ((1, 0), (2, .5), (3, 1)):
            clock.now = now
            result = await process(scan, session, sequence)
        assert result['connection_error']
        assert station.operation_id is None
        assert scan.snapshot()['tracks'][0]['state'] == 'failed'

    run_case(case, cfg)


def test_pause_discards_late_response_but_keeps_guide(cfg):
    async def case(scan, station, recognizer):
        started, release = Event(), Event()
        recognizer.detect_started = started
        recognizer.detect_release = release
        station.add_guides([cfg.slot_by_id(1)], 'manual-before-pause')
        pending_before = set(station.pending)
        operation_before = station.operation_id
        session = scan.start('phone')
        pending = asyncio.create_task(process(scan, session, 1))
        assert await asyncio.to_thread(started.wait, 2)
        scan.pause(session['session_id'])
        release.set()
        await pending
        assert scan.snapshot()['capture_state'] == 'paused'
        assert station.pending == pending_before
        assert station.operation_id == operation_before
        assert station.snapshot()['guide']['continuous'] is True

    run_case(case, cfg)


def test_pause_discards_late_classification(cfg):
    async def case(scan, station, recognizer):
        started, release = Event(), Event()
        recognizer.classify_started = started
        recognizer.classify_release = release
        session = scan.start('phone')
        clock = scan.clock
        clock.now = 0
        await process(scan, session, 1)
        clock.now = .5
        await process(scan, session, 2)
        clock.now = 1
        pending = asyncio.create_task(process(scan, session, 3))
        assert await asyncio.to_thread(started.wait, 2)
        scan.pause(session['session_id'])
        release.set()
        await pending
        assert station.operation_id is None
        assert scan.snapshot()['tracks'] == []
        assert scan.snapshot()['capture_state'] == 'paused'

    run_case(case, cfg)


def test_foreign_session_cannot_take_over(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone-a')
        with pytest.raises(ScanError) as foreign:
            scan.start('phone-b')
        assert foreign.value.status_code == 409
        scan.pause(session['session_id'])
        resumed = scan.start('phone-b', session['session_id'])
        assert resumed['generation'] > session['generation']
        assert resumed['session_id'] == session['session_id']

    run_case(case, cfg)


def test_pause_does_not_disable_sensors(cfg):
    async def case(scan, station, recognizer):
        started, release = Event(), Event()
        recognizer.detect_started = started
        recognizer.detect_release = release
        station.sensors.state[1] = False
        station.add_guides([cfg.slot_by_id(1)], 'cycle')
        session = scan.start('phone')
        pending = asyncio.create_task(process(scan, session, 1))
        assert await asyncio.to_thread(started.wait, 2)
        scan.pause(session['session_id'])
        previous = station.sensors.state[1]
        station.sensors.state[1] = True
        await station.on_sensor(1, previous, True)
        release.set()
        await pending
        assert station.confirmed == {1}
        assert not station.pending
        assert scan.snapshot()['capture_state'] == 'paused'

    run_case(case, cfg)


def test_sensor_changes_processed_while_colab_waits(cfg):
    async def case(scan, station, recognizer):
        started, release = Event(), Event()
        recognizer.detect_started = started
        recognizer.detect_release = release
        station.sensors.state[1] = False
        station.add_guides([cfg.slot_by_id(1)], 'running-cycle')
        session = scan.start('phone')
        pending = asyncio.create_task(process(scan, session, 1))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert scan.snapshot()['busy'] is True
            assert scan.snapshot()['capture_state'] == 'running'
            station.sensors.state[1] = True
            await station.on_sensor(1, False, True)
            assert station.confirmed == {1}
            assert not station.pending
            assert scan.snapshot()['busy'] is True
            assert scan.snapshot()['capture_state'] == 'running'
        finally:
            release.set()
            await pending
        assert scan.snapshot()['capture_state'] == 'running'

    run_case(case, cfg)


def test_completed_visible_category_is_suppressed(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        clock = scan.clock
        for sequence, now in ((1, 0), (2, .5), (3, 1), (4, 1.5)):
            clock.now = now
            await process(scan, session, sequence)
        assert station.pending == {7}
        assert recognizer.classify_calls == 1
        for sequence, now in ((5, 2), (6, 2.5), (7, 3), (8, 3.5)):
            clock.now = now
            await process(scan, session, sequence)
        assert recognizer.classify_calls == 1
        assert station.pending == {7}
        assert scan.snapshot()['tracks'][0]['category'] == 'servo_motor'

    run_case(case, cfg)


def test_explicit_qwen_rejection_is_not_overridden_by_local_thresholds(cfg):
    async def case(scan, station, recognizer):
        cfg.raw['decision'].update(min_similarity=.1, min_margin=.01)
        recognizer.recognized = False
        session = scan.start('phone')
        clock = scan.clock
        for sequence, now in ((1, 0), (2, .5), (3, 1), (4, 1.5)):
            clock.now = now
            await process(scan, session, sequence)
        assert station.pending == set()
        assert station.operation_id is None

    run_case(case, cfg)


def test_three_valid_absences_rearm(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        clock = scan.clock
        for sequence, now in ((1, 0), (2, .5), (3, 1), (4, 1.5)):
            clock.now = now
            await process(scan, session, sequence)
        recognizer.detections = []
        for sequence, now in ((5, 2), (6, 2.5)):
            clock.now = now
            await process(scan, session, sequence)
        assert 'servo_motor' in scan.snapshot()['suppressed_categories']
        clock.now = 3
        await process(scan, session, 7)
        assert 'servo_motor' not in scan.snapshot()['suppressed_categories']
        station.cancel_guide()
        recognizer.detections = None
        for sequence, now in ((8, 3.5), (9, 4), (10, 4.5), (11, 5)):
            clock.now = now
            await process(scan, session, sequence)
        assert station.pending == {7}
        assert recognizer.classify_calls == 2

    run_case(case, cfg)


def test_rearmed_category_does_not_reenter_same_cycle(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        clock = scan.clock
        for sequence, now in ((1, 0), (2, .5), (3, 1), (4, 1.5)):
            clock.now = now
            await process(scan, session, sequence)
        operation = station.operation_id
        assert station.pending == {7}

        recognizer.detections = []
        for sequence, now in ((5, 2), (6, 2.5), (7, 3)):
            clock.now = now
            await process(scan, session, sequence)
        assert 'servo_motor' not in scan.snapshot()['suppressed_categories']

        recognizer.detections = None
        for sequence, now in ((8, 3.5), (9, 4), (10, 4.5), (11, 5)):
            clock.now = now
            await process(scan, session, sequence)
        assert recognizer.classify_calls == 2
        assert station.operation_id == operation
        assert station.pending == {7}
        assert station.targets.keys() == {7}

    run_case(case, cfg)


def test_network_failure_does_not_rearm(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        clock = scan.clock
        for sequence, now in ((1, 0), (2, .5), (3, 1), (4, 1.5)):
            clock.now = now
            await process(scan, session, sequence)
        recognizer.fail_detect = True
        clock.now = 2
        await process(scan, session, 5)
        assert 'servo_motor' in scan.snapshot()['suppressed_categories']
        recognizer.fail_detect = False
        recognizer.detections = []
        for sequence, now in ((6, 3), (7, 4), (8, 5)):
            clock.now = now
            await process(scan, session, sequence)
        assert 'servo_motor' not in scan.snapshot()['suppressed_categories']

    run_case(case, cfg)


def test_reconnect_keeps_pending_and_suppression(cfg):
    async def case(scan, station, recognizer):
        first = scan.start('phone-a')
        clock = scan.clock
        for sequence, now in ((1, 0), (2, .5), (3, 1), (4, 1.5)):
            clock.now = now
            await process(scan, first, sequence)
        pending, operation = set(station.pending), station.operation_id
        assert 'servo_motor' in scan.snapshot()['suppressed_categories']
        scan.pause(first['session_id'])
        second = scan.start('phone-b', first['session_id'])
        assert second['generation'] > first['generation']
        assert station.pending == pending and station.operation_id == operation
        # Unknown geometry after a tracker reset is not proof that the old category disappeared.
        clock.now = 2
        await process(scan, second, 1)
        assert 'servo_motor' in scan.snapshot()['suppressed_categories']
        assert station.pending == pending and station.operation_id == operation

    run_case(case, cfg)


def test_backoff_is_bounded(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        clock = scan.clock
        recognizer.fail_detect = True
        expected = [1, 2, 4, 8, 15, 15]
        for sequence, delay in enumerate(expected, 1):
            clock.now = scan._retry_at or clock.now
            await process(scan, session, sequence)
            assert scan.snapshot()['retry_after_seconds'] == delay
        recognizer.fail_detect = False
        clock.now = scan._retry_at
        await process(scan, session, len(expected) + 1)
        assert scan.snapshot()['retry_after_seconds'] == 0

    run_case(case, cfg)


def test_storage_stays_bounded_during_outage(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        recognizer.fail_detect = True
        for sequence in range(1, 80):
            scan._retry_at = scan.clock()
            await process(scan, session, sequence)
        assert len(scan._current_crops) <= 12
        assert len(scan._corrections) <= 1
        assert scan._frame_jpeg is None
        assert not hasattr(scan, '_frame_queue')

    run_case(case, cfg)


def test_manual_recognition_cannot_replace_continuous_guide(cfg):
    from fastapi.testclient import TestClient
    from kiwi_pi.app import create_app
    app = create_app(cfg)
    with TestClient(app) as client:
        # A continuous guide must survive all legacy manual cancellation paths.
        app_scan = app.state.scan if hasattr(app.state, 'scan') else None
        assert app_scan is not None
        app_scan.station.add_guides([cfg.slot_by_id(1)], 'continuous-test')
        photo = client.post('/api/upload', files={'file': ('a.jpg', jpeg_bytes(), 'image/jpeg')}).json()
        result = client.post('/api/recognize', json={'photo_id': photo['photo_id']})
        assert result.status_code == 409
        assert client.post('/api/next').status_code == 409
        assert client.post('/api/reject', json={'photo_id': photo['photo_id'],
                                                'object_id': 1, 'actual': 'otros'}).status_code == 409
        assert app_scan.station.operation_id == 'continuous-test'
        assert app_scan.station.pending == {1}


def test_frozen_crop_survives_pause_generation(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        clock = scan.clock
        clock.now = 0
        frame = scan_frame_bytes()
        await process(scan, session, 1, frame)
        track = scan.snapshot()['tracks'][0]
        frozen = scan.freeze_correction(session['session_id'], track['track_id'], track['revision'])
        assert frozen['generation'] > session['generation']
        assert scan.snapshot()['capture_state'] == 'paused'
        assert scan.correction_crop(frozen['correction_id'])
        assert scan.correction_crop(frozen['correction_id'])
        resumed = scan.start('phone', session['session_id'])
        await process(scan, resumed, 1)
        track = scan.snapshot()['tracks'][0]
        with pytest.raises(ScanError) as duplicate:
            scan.freeze_correction(resumed['session_id'], track['track_id'], track['revision'])
        assert duplicate.value.status_code == 409
        assert len(scan._corrections) == 1

    run_case(case, cfg)


def test_expired_or_foreign_correction_saves_nothing(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        await process(scan, session, 1)
        track = scan.snapshot()['tracks'][0]
        with pytest.raises(ScanError) as foreign:
            scan.freeze_correction('foreign-session', track['track_id'], track['revision'])
        assert foreign.value.status_code == 409
        frozen = scan.freeze_correction(session['session_id'], track['track_id'], track['revision'])
        scan.clock.now += 301
        with pytest.raises(ScanError) as expired:
            scan.submit_correction(frozen['correction_id'], 'solder')
        assert expired.value.status_code == 410
        assert not list((cfg.data_dir / 'feedback').rglob('*.jpg'))

    run_case(case, cfg)


def test_correction_saves_only_crop(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        frame = scan_frame_bytes()
        await process(scan, session, 1, frame)
        track = scan.snapshot()['tracks'][0]
        frozen = scan.freeze_correction(session['session_id'], track['track_id'], track['revision'])
        crop = scan.correction_crop(frozen['correction_id'])
        scan.submit_correction(frozen['correction_id'], 'solder')
        files = list((cfg.data_dir / 'feedback' / 'solder').glob('*.jpg'))
        metadata = list((cfg.data_dir / 'feedback' / 'solder').glob('*.json'))
        assert len(files) == len(metadata) == 1
        with Image.open(files[0]) as saved:
            assert saved.size == (44, 44)
        assert files[0].read_bytes() == crop
        payload = json.loads(metadata[0].read_text())
        assert payload['actual'] == 'solder'
        assert not list(cfg.data_dir.glob('*scan*.jpg'))

    run_case(case, cfg)


def test_frozen_correction_cannot_cancel_successor_guide_in_same_generation(cfg):
    async def case(scan, station, recognizer):
        session = scan.start('phone')
        await process(scan, session, 1)
        track = scan.snapshot()['tracks'][0]
        frozen = scan.freeze_correction(session['session_id'], track['track_id'], track['revision'])
        station.begin_guides([cfg.slot_by_id(1)], 'successor-manual')
        before = scan.snapshot()
        with pytest.raises(ScanError) as stale:
            scan.submit_correction(frozen['correction_id'], 'solder')
        assert stale.value.status_code == 409
        assert scan.snapshot() == before
        assert station.operation_id == 'successor-manual'
        assert station.pending == {1}
        assert not list((cfg.data_dir / 'feedback').rglob('*.jpg'))
        assert scan.correction_crop(frozen['correction_id'])
        scan.cancel_correction(frozen['correction_id'])
        assert station.operation_id == 'successor-manual'

    run_case(case, cfg)
