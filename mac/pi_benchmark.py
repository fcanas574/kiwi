"""Run on the Pi: real HTTPS model calls, mock LEDs, artificial reference scene."""
import asyncio
import json
from pathlib import Path
import shlex
import sys
import tempfile
import time

import yaml

PI = Path.home() / 'kiwi-pruebas/pi'
sys.path.insert(0, str(PI))
from kiwi_pi.config import Config
from kiwi_pi.recognizer import RestRecognizer
from kiwi_pi.scan import ScanCoordinator
from kiwi_pi.station import Station


async def main():
    raw = yaml.safe_load((PI / 'config.yaml').read_text())
    lines = (PI / '.env').read_text().splitlines()
    raw['recognizer']['api_key'] = [shlex.split(line.split('=', 1)[1])[0] for line in lines if line.startswith('KIWI_API_KEY=')][0]
    raw['leds']['driver'] = 'mock'
    raw['sensors']['driver'] = 'none'
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(raw, Path(tmp))
        model = RestRecognizer(cfg)
        station = Station(cfg, model)
        scan = ScanCoordinator(station, model, cfg, time.monotonic)
        sample = Path('/tmp/kiwi-mac-benchmark.jpg').read_bytes()
        report = {'field_validation': False, 'hardware_led_test': False,
                  'sample': 'artificial three-reference-photo montage', 'transport': 'Pi → HTTPS/Wi-Fi → Mac',
                  'runs': []}
        try:
            assert (await model.health()).online and model.supports_batch
            for run in range(5):
                scan.finish_station_cycle()
                session = scan.start('benchmark')
                began = time.perf_counter()
                timings = []
                for sequence in range(1, 8):
                    start = time.perf_counter()
                    response = await scan.process_frame(session['session_id'], session['generation'], sequence, sample)
                    timings.append({'frame': sequence,'detect': response['detection_seconds'],'classify': response['classification_seconds'],'total': round(time.perf_counter()-start,3)})
                    if response.get('error'):
                        raise RuntimeError(response['error'])
                    if {1, 6, 7}.issubset(station.pending):
                        break
                    await asyncio.sleep(max(0, 1 / cfg.section('scan').get('max_fps', 1) - (time.perf_counter() - start)))
                row = {'run': run + 1, 'seconds_to_all_three': round(time.perf_counter() - began, 3),
                       'all_three_confirmed': {1, 6, 7}.issubset(station.pending),
                       'unexpected_slots': sorted(station.pending - {1, 6, 7}), 'timings': timings}
                report['runs'].append(row)
                print(json.dumps(row), flush=True)
            # Exercise quick-one against a fresh crop from the same JPEG.
            from PIL import Image
            import io
            with Image.open(io.BytesIO(sample)) as image:
                crop = image.crop((32, 20, 320, 440))
                buffer = io.BytesIO()
                crop.save(buffer, 'JPEG')
            scan.finish_station_cycle()
            cfg.raw['scan']['mode'] = 'single'
            scan.tracker.reset()
            session = scan.start('benchmark')
            began = time.perf_counter()
            for sequence in range(1, 5):
                start = time.perf_counter()
                await scan.process_frame(session['session_id'], session['generation'], sequence, buffer.getvalue())
                if 1 in station.pending:
                    break
                await asyncio.sleep(max(0, 1 / cfg.section('scan').get('max_fps', 1) - (time.perf_counter() - start)))
            report['quick_one'] = {'confirmed': 1 in station.pending,
                                   'seconds': round(time.perf_counter() - began, 3)}
            print(json.dumps(report['quick_one']), flush=True)
            Path('/tmp/kiwi-mac-benchmark-results.json').write_text(json.dumps(report, indent=2))
        finally:
            await scan.close()
            await station.stop()


if __name__ == '__main__':
    asyncio.run(main())
