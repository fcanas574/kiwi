import asyncio

import pytest

from test_station import cfg
from kiwi_pi.recognizer import MockRecognizer
from kiwi_pi.station import Station


def run_case(cfg, case):
    async def run():
        st = Station(cfg, MockRecognizer(cfg))
        try:
            await case(st)
        finally:
            await st.stop()

    asyncio.run(run())


async def sensor(st, slot, occupied):
    previous = st.sensors.state.get(slot)
    st.sensors.state[slot] = occupied
    await st.on_sensor(slot, previous, occupied)


def test_add_target_keeps_confirmed_and_wrong_slots(cfg):
    async def case(st):
        st.sensors.state.update({1: False, 5: False, 7: False})
        st.add_guides([cfg.slot_by_id(1)], 'cycle-a')
        await sensor(st, 5, True)
        await sensor(st, 1, True)

        # Add a new destination while the confirmed target is green and a wrong
        # slot is still occupied.
        st.add_guides([cfg.slot_by_id(7)], 'cycle-a')

        assert st.confirmed == {1}
        assert st.pending == {7}
        assert st.wrong_slots == {5}
        assert st.operation_id == 'cycle-a'
        assert st.phase == 'wrong_slot'
        assert st.leds.modes[1] == 'ok'
        assert st.leds.modes[5] == 'wrong'
        assert st.leds.modes[7] == 'guide'

    run_case(cfg, case)


def test_new_target_checks_current_sensor_occupancy(cfg):
    async def case(st):
        st.sensors.state.update({1: False, 7: True})
        st.add_guides([cfg.slot_by_id(1)], 'cycle-a')
        await sensor(st, 1, True)

        # Slot 7 is already occupied when it becomes a target. It must be
        # blocked until it is cleared and occupied again during this cycle.
        st.add_guides([cfg.slot_by_id(7)], 'cycle-a')
        assert st.blocked == {7}
        assert st.confirmed == {1}
        assert st.pending == {7}
        await asyncio.sleep(max(st.ok_seconds, st.auto_reset) + 0.05)
        assert st.phase == 'guiding'
        assert st.confirmed == {1}
        assert st.pending == {7}
        await st.on_sensor(7, True, True)
        assert st.confirmed == {1}
        await sensor(st, 7, False)
        await sensor(st, 7, True)
        assert st.confirmed == {1, 7}

    run_case(cfg, case)


def test_duplicate_target_does_not_reset_progress(cfg):
    async def case(st):
        st.sensors.state[1] = False
        st.add_guides([cfg.slot_by_id(1)], 'cycle-a')
        await sensor(st, 1, True)
        assert st.phase == 'returned'
        timer = st._timer

        st.add_guides([cfg.slot_by_id(1)], 'cycle-a')

        assert st.confirmed == {1}
        assert not st.pending
        assert st.phase == 'returned'
        assert st._timer is timer

    run_case(cfg, case)


def test_continuous_guide_does_not_expire(cfg):
    cfg.raw['guide']['guide_timeout_seconds'] = 0.01

    async def case(st):
        st.sensors.state[1] = False
        st.add_guides([cfg.slot_by_id(1)], 'cycle-a')
        await asyncio.sleep(0.04)

        guide = st.snapshot()['guide']
        assert guide['continuous'] is True
        assert guide['phase'] == 'guiding'
        assert guide['pending'] == [1]
        assert guide['remaining'] is None

    run_case(cfg, case)


def test_old_success_timer_cannot_cancel_new_cycle(cfg):
    async def case(st):
        st.sensors.state.update({1: False, 7: False})
        st.add_guides([cfg.slot_by_id(1)], 'cycle-a')
        await sensor(st, 1, True)
        assert st.phase == 'returned'

        # A different operation starts a new cycle during the old green timer.
        st.add_guides([cfg.slot_by_id(7)], 'cycle-b')
        await asyncio.sleep(max(st.ok_seconds, st.auto_reset) + 0.05)

        assert st.phase == 'guiding'
        assert st.operation_id == 'cycle-b'
        assert st.pending == {7}
        assert st.confirmed == set()
        assert st.leds.modes[7] == 'guide'

    run_case(cfg, case)


def test_no_sensors_never_completes(cfg):
    cfg.raw['sensors']['driver'] = 'none'

    async def case(st):
        st.add_guides([cfg.slot_by_id(1)], 'cycle-a')
        await sensor(st, 1, True)

        guide = st.snapshot()['guide']
        assert guide['verify'] is False
        assert guide['confirmed'] == []
        assert guide['pending'] == [1]
        assert guide['phase'] == 'guiding'

    run_case(cfg, case)
