"""Stop only this installation's model server, then release its launcher lock."""
import os
from pathlib import Path
import signal
import subprocess

ROOT = Path(__file__).resolve().parent
pidfile = ROOT / '.server.pid'
if not pidfile.exists():
    print('Kiwi local está detenido.')
    raise SystemExit(0)
pid = int(pidfile.read_text())
command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout
if str(ROOT / 'server.py') not in command:
    raise SystemExit('El proceso ya cambió; no se detuvo otro programa.')
os.kill(pid, signal.SIGTERM)
print('Deteniendo el reconocimiento local de Kiwi…')
