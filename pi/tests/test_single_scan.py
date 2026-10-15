import asyncio
import io
import time

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from kiwi_pi.app import create_app
from kiwi_pi.recognizer import MockRecognizer
from kiwi_pi.scan import ScanCoordinator, ScanError
from kiwi_pi.station import Station
from test_station import cfg


def jpeg():
    buffer = io.BytesIO()
    Image.new('RGB',(100,100),'red').save(buffer,'JPEG')
    return buffer.getvalue()


def setup(cfg):
    cfg.raw['scan']['mode'] = 'single'
    cfg.raw['sensors']['driver'] = 'none'
    recognizer = MockRecognizer(cfg)
    station = Station(cfg,recognizer)
    scan = ScanCoordinator(station,recognizer,cfg,time.monotonic)
    return recognizer,station,scan


def test_fast_scan_requires_two_matching_classifications_without_detector(cfg):
    async def run():
        recognizer,station,scan = setup(cfg)
        async def no_detector(*args):
            raise AssertionError('Single mode must not call DINO')
        recognizer.detect = no_detector
        session = scan.start('station')
        try:
            first = await scan.process_frame(session['session_id'],session['generation'],1,jpeg())
            assert first['tracks'][0]['state'] == 'provisional'
            assert not station.pending
            second = await scan.process_frame(session['session_id'],session['generation'],2,jpeg())
            assert second['tracks'][0]['state'] == 'accepted'
            assert station.pending == {1}
            assert not second['busy']
        finally:
            await scan.close(); await station.stop()
    asyncio.run(run())


def test_different_or_rejected_scene_resets_the_two_image_confirmation(cfg):
    async def run():
        recognizer,station,scan = setup(cfg)
        session = scan.start('station')
        try:
            for sequence,category in enumerate(['digital_multimeter','servo_motor','otros','servo_motor'],1):
                recognizer.force = category
                await scan.process_frame(session['session_id'],session['generation'],sequence,jpeg())
                assert not station.pending
            result = await scan.process_frame(session['session_id'],session['generation'],5,jpeg())
            assert result['tracks'][0]['category'] == 'servo_motor'
            assert station.pending == {7}
        finally:
            await scan.close(); await station.stop()
    asyncio.run(run())


def test_pause_invalidates_classification_in_flight_and_fresh_start_requires_two_frames(cfg):
    async def run():
        recognizer,station,scan = setup(cfg)
        original = recognizer.classify
        entered,release = asyncio.Event(),asyncio.Event()
        async def blocked(data,correlation):
            entered.set(); await release.wait()
            return await original(data,correlation)
        session = scan.start('station')
        try:
            await scan.process_frame(session['session_id'],session['generation'],1,jpeg())
            recognizer.classify = blocked
            task = asyncio.create_task(scan.process_frame(session['session_id'],session['generation'],2,jpeg()))
            await entered.wait()
            with pytest.raises(ScanError):
                await scan.process_frame(session['session_id'],session['generation'],3,jpeg())
            scan.pause(session['session_id'])
            release.set()
            assert (await task)['discarded']
            assert not station.pending
            recognizer.classify = original
            fresh = scan.start('station')
            await scan.process_frame(fresh['session_id'],fresh['generation'],1,jpeg())
            assert not station.pending
            await scan.process_frame(fresh['session_id'],fresh['generation'],2,jpeg())
            assert station.pending == {1}
        finally:
            await scan.close(); await station.stop()
    asyncio.run(run())


def test_only_local_station_can_change_modes_and_changing_clears_guides(cfg):
    cfg.raw['server']['allow_remote_admin'] = False
    app = create_app(cfg)
    with TestClient(app,client=('127.0.0.1',1000)) as station:
        phone = TestClient(app,client=('192.168.1.4',1001))
        try:
            assert phone.put('/api/station/scan/mode',json={'mode':'single'}).status_code == 403
            app.state.scan.station.add_guides([cfg.slot_by_id(1)],'cycle')
            response = station.put('/api/station/scan/mode',json={'mode':'single'})
            assert response.status_code == 200
            assert response.json()['mode'] == 'single'
            assert not app.state.scan.station.pending
            assert cfg._load_settings()['scan_mode'] == 'single'
            assert station.put('/api/station/scan/mode',json={'mode':'invalid'}).status_code == 400
        finally:
            phone.close()
