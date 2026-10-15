"""Run one model server instance, holding an exclusive local lock."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
lock = open(ROOT / '.server.lock', 'a')
os.chmod(ROOT / '.server.lock', 0o600)
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print('Kiwi ya está ejecutándose en este Mac.')
    raise SystemExit(0)
environment = os.environ.copy()
environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTORCH_ENABLE_MPS_FALLBACK='1')
print('Iniciando Kiwi local. Mantén el Mac encendido y conectado al mismo Wi-Fi.', flush=True)
with open(ROOT / 'results/server.log', 'a') as log:
    process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'server.py')], env=environment,
                               stdout=log, stderr=log)
    pidfile = ROOT / '.server.pid'
    pidfile.write_text(str(process.pid))
    pidfile.chmod(0o600)
    try:
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
    finally:
        if pidfile.exists() and pidfile.read_text() == str(process.pid):
            pidfile.unlink()
    raise SystemExit(process.returncode)
