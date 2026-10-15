#!/usr/bin/env python3
"""Apply the camera URL repair to testuser's already configured installation."""
from datetime import datetime
from pathlib import Path
import re
import shutil
import subprocess
import sys

HOME = Path.home()
SOURCE = Path(__file__).resolve().parent
PI = HOME / 'kiwi-pruebas/pi'
TOOLS = HOME / 'kiwi-tools'
UNIT = HOME / '.config/systemd/user/kiwi.service'
PROXY = TOOLS / 'local_screen_proxy.py'


def call(*args):
    subprocess.run(args, check=True, timeout=120)


def main():
    # Validate known installation before changing anything; never touch .env.
    unit = UNIT.read_text()
    proxy = PROXY.read_text()
    if not (PI / '.venv/bin/python').exists() or 'ExecStart=' not in unit:
        raise SystemExit('No se encontró la instalación de Kiwi esperada.')
    proxy, changes = re.subn(r'(?m)^HOSTNAME\s*=\s*[^\n]+$', "HOSTNAME = 'kite.local'", proxy)
    if changes != 1 or '127.0.0.1' not in proxy:
        raise SystemExit('El puente local no coincide con la instalación esperada; no se cambió nada.')
    # Isolated certificate/IP tests use temporary files, not the installed CA.
    call(str(PI / '.venv/bin/python'), str(SOURCE / 'test_prepare_network.py'))
    instruction = f'ExecStartPre={PI}/.venv/bin/python {TOOLS}/prepare_network.py'
    if instruction not in unit:
        unit = unit.replace('[Service]\n', '[Service]\n' + instruction + '\n', 1)
    backup = TOOLS / ('camera-link-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True, mode=0o700)
    for index, path in enumerate((UNIT, PROXY, TOOLS / 'phone_setup.py', PI / 'config.yaml')):
        if path.exists():
            shutil.copy2(path, backup / (str(index) + '-' + path.name))
    # Stop recognition first; graceful shutdown clears LEDs and old pairing.
    call('systemctl', '--user', 'stop', 'kiwi-kiosk.service', 'kiwi-local-screen.service', 'kiwi.service')
    for filename in ('prepare_network.py', 'phone_setup.py'):
        shutil.copy2(SOURCE / filename, TOOLS / filename)
    for filename in ('station.html', 'station.css', 'station.js'):
        target = PI / 'kiwi_pi/static' / filename
        shutil.copy2(target, backup / filename)
        shutil.copy2(SOURCE / filename, target)
    for filename in ('scan.py', 'recognizer.py'):
        if (SOURCE / filename).exists():
            target = PI / 'kiwi_pi' / filename
            shutil.copy2(target, backup / filename)
            shutil.copy2(SOURCE / filename, target)
    PROXY.write_text(proxy)
    UNIT.write_text(unit)
    call(str(PI / '.venv/bin/python'), str(TOOLS / 'prepare_network.py'))
    shutil.copy2(SOURCE / 'Guia_Kiwi.md', HOME / 'Desktop/Guia_Kiwi.md')
    call('systemctl', '--user', 'daemon-reload')
    call('systemctl', '--user', 'restart', 'kiwi.service', 'kiwi-phone-setup.service', 'kiwi-local-screen.service', 'kiwi-kiosk.service')
    call('systemctl', '--user', 'is-active', 'kiwi.service', 'kiwi-local-screen.service', 'kiwi-kiosk.service', 'kiwi-phone-setup.service')
    print('\nListo: conexión de cámara corregida. Toca «Cámara» en la Raspberry y vuelve a conectar el iPhone.')
    print((HOME / 'kiwi-https-pruebas/camera-url.txt').read_text().strip())


if __name__ == '__main__':
    main()
