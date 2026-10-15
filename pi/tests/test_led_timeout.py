import asyncio

from kiwi_pi.recognizer import MockRecognizer
from kiwi_pi.station import Station
from kiwi_pi.scan import ScanCoordinator
from test_station import cfg
import time


def prepare(cfg, duration=.10):
    cfg.raw['sensors']['driver'] = 'none'
    cfg.raw['guide']['continuous_led_seconds'] = duration
    return Station(cfg, MockRecognizer(cfg))


def test_each_object_expires_from_its_own_recognition_time(cfg):
    async def run():
        station = prepare(cfg)
        try:
            station.add_guides([cfg.slot_by_id(1)],'cycle')
            await asyncio.sleep(.06)
            station.add_guides([cfg.slot_by_id(7)],'cycle')
            await asyncio.sleep(.06)
            assert station.leds.modes[1] == 'off'
            assert station.leds.modes[7] == 'guide'
            assert station.pending == {7}
            await asyncio.sleep(.06)
            assert station.leds.modes[7] == 'off'
            assert station.operation_id is None
        finally:
            await station.stop()
    asyncio.run(run())


def test_old_timeout_cannot_extinguish_new_cycle(cfg):
    async def run():
        station = prepare(cfg)
        try:
            station.add_guides([cfg.slot_by_id(1)],'old')
            await asyncio.sleep(.06)
            station.cancel_guide()
            station.add_guides([cfg.slot_by_id(1)],'new')
            await asyncio.sleep(.06)
            assert station.leds.modes[1] == 'guide'
            assert station.operation_id == 'new'
            await asyncio.sleep(.06)
            assert station.leds.modes[1] == 'off'
        finally:
            await station.stop()
    asyncio.run(run())


def test_pause_keeps_countdown_and_finish_cancels_it(cfg):
    async def run():
        station = prepare(cfg)
        scan = ScanCoordinator(station, station.recognizer, cfg, time.monotonic)
        try:
            started = scan.start('station')
            station.add_guides([cfg.slot_by_id(1)],'cycle')
            scan.pause(started['session_id'])
            assert station.snapshot()['slots'][0]['led_remaining'] == 1
            await asyncio.sleep(.12)
            assert station.leds.modes[1] == 'off'
            station.add_guides([cfg.slot_by_id(7)],'new')
            scan.finish_station_cycle()
            assert not station._slot_timers
            assert not station._slot_deadlines
            assert all(slot['led'] == 'off' for slot in station.snapshot()['slots'])
        finally:
            await scan.close()
            await station.stop()
    asyncio.run(run())


def test_timer_does_not_rearm_an_object_that_is_still_visible(cfg):
    async def run():
        station = prepare(cfg)
        scan = ScanCoordinator(station, station.recognizer, cfg, time.monotonic)
        try:
            slot = cfg.slot_by_id(1)
            scan.start('station')
            recognition = {'recognized':True,'candidate':slot.category,'ranking':[],
                           'similarity':1.0,'margin':1.0,'reason':'test','mode':'vlm'}
            confirmed = [{'recognition':recognition}]
            scan._apply_confirmed(confirmed)
            assert station.leds.modes[1] == 'guide'
            await asyncio.sleep(.12)
            assert station.leds.modes[1] == 'off'
            scan._sync_cycle()
            scan._apply_confirmed(confirmed)
            assert station.leds.modes[1] == 'off'
            assert slot.category in scan.snapshot()['suppressed_categories']
        finally:
            await scan.close()
            await station.stop()
    asyncio.run(run())
