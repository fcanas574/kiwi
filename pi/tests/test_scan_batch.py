import asyncio

from test_scan import cfg, station_cfg, make_scan, process, ScriptedRecognizer


class BatchRecognizer(ScriptedRecognizer):
    supports_batch = True

    def __init__(self):
        super().__init__()
        self.batches = []
        self.bad_correlation = False
        self.bad_track = 2
        self.started = None
        self.release = None
        self.detections = [{'id': i, 'box': [x, 1, x + 24, 25],
                            'detector_label': 'tool', 'detector_score': .9}
                           for i, x in enumerate((1, 35, 70), 1)]

    async def classify_batch(self, items):
        self.batches.append(items)
        if self.started:
            self.started.set()
            await self.release.wait()
        categories = ('digital_multimeter', 'servo_motor', 'solder')
        results = []
        for _, info in items:
            category = categories[info['track_id'] - 1] if info['track_id'] <= 3 else 'otros'
            echoed = dict(info)
            if self.bad_correlation and info['track_id'] == self.bad_track:
                echoed['frame_id'] = 'wrong'
            results.append({'correlation': echoed, 'result': {
                'candidate': category, 'recognized': category != 'otros', 'similarity': .98, 'margin': .95,
                'reason': 'fixture', 'ranking': [{'object': category, 'similarity': .98}]}})
        return results


def test_three_tracks_classified_together_then_confirmed_on_later_frame(cfg):
    async def case():
        scan, station, model = make_scan(cfg, BatchRecognizer())
        try:
            session = scan.start('phone')
            for sequence in range(1, 4):
                scan.clock.now = (sequence - 1) * .5
                await process(scan, session, sequence)
            assert len(model.batches) == 1 and len(model.batches[0]) == 3
            assert {t['state'] for t in scan.snapshot()['tracks']} == {'provisional'}
            assert not station.pending
            scan.clock.now = 1.5
            await process(scan, session, 4)
            assert station.pending == {1, 6, 7}
            assert len(model.batches) == 1  # accepted objects are not reclassified
        finally:
            await scan.close()
            await station.stop()
    asyncio.run(case())


def test_bad_member_rejects_entire_batch_without_partial_acceptance(cfg):
    async def case():
        model = BatchRecognizer()
        model.bad_correlation = True
        scan, station, _ = make_scan(cfg, model)
        try:
            session = scan.start('phone')
            for sequence in range(1, 4):
                scan.clock.now = (sequence - 1) * .5
                await process(scan, session, sequence)
            assert not station.pending
            assert all(t['recognition'] is None for t in scan.snapshot()['tracks'])
            assert scan.snapshot()['connection_error']
        finally:
            await scan.close()
            await station.stop()
    asyncio.run(case())


def test_finishing_discards_all_inflight_batch_results(cfg):
    async def case():
        model = BatchRecognizer()
        model.started, model.release = asyncio.Event(), asyncio.Event()
        scan, station, _ = make_scan(cfg, model)
        try:
            session = scan.start('phone')
            for sequence in range(1, 3):
                scan.clock.now = (sequence - 1) * .5
                await process(scan, session, sequence)
            scan.clock.now = 1.0
            request = asyncio.create_task(process(scan, session, 3))
            await model.started.wait()
            scan.finish_station_cycle()
            model.release.set()
            result = await request
            assert result['discarded']
            assert not station.pending and not scan.snapshot()['tracks']
        finally:
            await scan.close()
            await station.stop()
    asyncio.run(case())


def test_second_batch_failure_does_not_accept_first_batch(cfg):
    async def case():
        model = BatchRecognizer()
        model.detections.extend({'id': i, 'box': [x, 40, x + 24, 64],
                                'detector_label': 'clutter', 'detector_score': .8}
                               for i, x in enumerate((1, 35, 70), 4))
        model.bad_correlation, model.bad_track = True, 5
        scan, station, _ = make_scan(cfg, model)
        try:
            session = scan.start('phone')
            for sequence in range(1, 4):
                scan.clock.now = (sequence - 1) * .5
                await process(scan, session, sequence)
            assert [len(batch) for batch in model.batches] == [3, 3]
            assert not station.pending
            assert all(t['recognition'] is None for t in scan.snapshot()['tracks'])
        finally:
            await scan.close()
            await station.stop()
    asyncio.run(case())
