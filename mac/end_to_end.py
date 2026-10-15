"""Exercise real models through REST and the Pi scan logic with mock GPIO.

The scene is an artificial reference montage; no field-accuracy claim is made.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

import httpx
import yaml

from engine import REPO
from server import create_app

sys.path.insert(0, str(REPO / 'pi'))
from kiwi_pi.config import Config
from kiwi_pi.recognizer import RestRecognizer
from kiwi_pi.scan import ScanCoordinator
from kiwi_pi.station import Station


async def main():
    key = 'isolated-local-model-test'
    app = create_app(key=key)
    async with app.router.lifespan_context(app):
        with tempfile.TemporaryDirectory() as tmp:
            raw = yaml.safe_load((REPO / 'pi/config.example.yaml').read_text())
            raw['recognizer'].update(url='http://localhost/predict', api_key=key)
            raw['leds']['driver'] = 'mock'
            raw['sensors']['driver'] = 'none'
            raw['scan'].update(mode='tracked', stable_samples=2, stable_seconds=.35,
                               stable_iou=.5, area_tolerance=.3, center_tolerance=.04)
            raw['guide']['continuous_led_seconds'] = 20
            cfg = Config(raw, Path(tmp))
            model = RestRecognizer(cfg)
            await model.client.aclose()
            model.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost')
            station = Station(cfg, model)
            scan = ScanCoordinator(station, model, cfg, time.monotonic)
            sample = (REPO / 'mac/results/three-reference-montage.jpg').read_bytes()
            report = {'field_validation': False, 'sample': 'artificial three-photo montage', 'runs': []}
            try:
                assert (await model.health()).online and model.supports_batch
                # First actual-aspect-ratio detection primes the MPS shape.
                await model.detect(sample, {'warmup': True})
                for run in range(3):
                    scan.finish_station_cycle()
                    session = scan.start('benchmark')
                    began = time.perf_counter()
                    frames = []
                    for sequence in range(1, 10):
                        frame_start = time.perf_counter()
                        response = await scan.process_frame(session['session_id'], session['generation'], sequence, sample)
                        frames.append({'sequence': sequence, 'elapsed': round(time.perf_counter() - began, 3),
                                       'pending_slots': sorted(station.pending),
                                       'tracks': response['tracks'], 'error': response.get('error')})
                        if {1, 6, 7}.issubset(station.pending):
                            break
                        await asyncio.sleep(max(0, 1 - (time.perf_counter() - frame_start)))
                    elapsed = time.perf_counter() - began
                    row = {'run': run + 1, 'seconds_to_all_three': round(elapsed, 3),
                           'all_three_confirmed': {1, 6, 7}.issubset(station.pending),
                           'unexpected_slots': sorted(station.pending - {1, 6, 7}), 'frames': frames}
                    report['runs'].append(row)
                    print({key: value for key, value in row.items() if key != 'frames'}, flush=True)
                (REPO / 'mac/results/local-end-to-end.json').write_text(json.dumps(report, indent=2))
            finally:
                await scan.close()
                await station.stop()


if __name__ == '__main__':
    asyncio.run(main())
