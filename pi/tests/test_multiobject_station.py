import asyncio
import itertools
import pytest
from test_station import cfg
from kiwi_pi.station import Station
from kiwi_pi.recognizer import MockRecognizer


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


@pytest.mark.parametrize('order', list(itertools.permutations([1, 2, 3])))
def test_three_returns_in_any_order(cfg, order):
    async def case(st):
        for i in (1, 2, 3): st.sensors.state[i] = False
        st.begin_guides([cfg.slot_by_id(i) for i in (1, 2, 3)], 'photo-a')
        assert [st.leds.modes[i] for i in (1, 2, 3)] == ['guide'] * 3
        for count, slot in enumerate(order, 1):
            await sensor(st, slot, True)
            guide = st.snapshot()['guide']
            assert len(guide['confirmed']) == count
            assert len(guide['pending']) == 3 - count
            assert st.phase == ('returned' if count == 3 else 'guiding')
            assert st.leds.modes[slot] == 'ok'
        assert guide['operation_id'] == 'photo-a'
    run_case(cfg, case)


def test_preoccupied_and_removal_need_new_return(cfg):
    async def case(st):
        st.sensors.state.update({1: True, 2: False})
        st.begin_guides([cfg.slot_by_id(1), cfg.slot_by_id(2)], 'a')
        await st.on_sensor(1, None, True)
        assert st.snapshot()['guide']['blocked'] == [1]
        assert st.snapshot()['guide']['confirmed'] == []
        await sensor(st, 1, False)
        await sensor(st, 1, True)
        await sensor(st, 1, False)
        assert st.snapshot()['guide']['pending'] == [1, 2]
        assert st.leds.modes[1] == 'guide'
    run_case(cfg, case)


def test_all_targets_wait_for_all_wrong_slots_to_clear(cfg):
    async def case(st):
        st.sensors.state.update({1: False, 2: False, 5: False, 6: False})
        st.begin_guides([cfg.slot_by_id(1), cfg.slot_by_id(2)], 'a')
        await sensor(st, 5, True); await sensor(st, 6, True)
        await sensor(st, 1, True); await sensor(st, 2, True)
        assert st.phase == 'wrong_slot'
        assert st.snapshot()['guide']['confirmed'] == [1, 2]
        await sensor(st, 6, False)
        assert st.snapshot()['guide']['wrong_slots'] == [5]
        assert st.leds.modes[1] == 'ok'
        await sensor(st, 5, False)
        assert st.phase == 'returned'
    run_case(cfg, case)


def test_timeout_retains_progress_and_cancel_clears_it(cfg):
    async def case(st):
        st.sensors.state.update({1: False, 2: False})
        st.begin_guides([cfg.slot_by_id(1), cfg.slot_by_id(2)], 'a')
        await sensor(st, 1, True)
        st._on_timeout()
        assert st.snapshot()['guide']['pending'] == [2]
        assert st.snapshot()['guide']['confirmed'] == [1]
        assert all(mode == 'off' for mode in st.leds.modes.values())
        st.cancel_guide()
        assert st.snapshot()['guide']['targets'] == []
        assert st.snapshot()['guide']['operation_id'] is None
    run_case(cfg, case)


def test_no_sensor_does_not_claim_confirmation(cfg):
    cfg.raw['sensors']['driver'] = 'none'
    async def case(st):
        st.begin_guides([cfg.slot_by_id(1)], 'a')
        await sensor(st, 1, True)
        assert not st.snapshot()['guide']['verify']
        assert st.snapshot()['guide']['confirmed'] == []
        assert st.phase == 'guiding'
    run_case(cfg, case)


@pytest.mark.parametrize('remove_target', [True, False])
@pytest.mark.parametrize('continuous', [False, True])
def test_completion_waits_for_entire_sensor_sample(cfg, remove_target, continuous):
    async def case(st):
        st.sensors.debounce = 0
        start = st.add_guides if continuous else st.begin_guides
        start([cfg.slot_by_id(1), cfg.slot_by_id(7)], 'batch')
        st.simulate_sensor(7, True)
        await st.sensors.tick(); await st.sensors.tick()
        assert st.confirmed == {7}
        st.simulate_sensor(1, True)
        st.simulate_sensor(7 if remove_target else 8, not remove_target)
        await st.sensors.tick(); await st.sensors.tick()
        if remove_target:
            assert st.phase == 'guiding'
            assert st.pending == {7}
            assert st.confirmed == {1}
        else:
            assert st.phase == 'wrong_slot'
            assert st.wrong_slots == {8}
            assert st.confirmed == {1, 7}
    run_case(cfg, case)
