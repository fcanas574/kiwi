#!/usr/bin/env python3
"""Prepare this installation's current Wi-Fi URL without relying on mDNS.

Run before Kiwi starts. Reuses the trusted CA; private keys never leave the Pi.
The loopback screen proxy uses kite.local only as TLS SNI, not for DNS lookup.
"""
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time

import yaml


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=20, **kwargs).stdout


def find_address():
    ip = shutil.which('ip') or '/usr/sbin/ip'
    entries = json.loads(run(ip, '-j', '-4', 'address', 'show', 'scope', 'global'))
    candidates = []
    for entry in entries:
        if 'UP' not in entry.get('flags', []):
            continue
        name = entry.get('ifname', '')
        for item in entry.get('addr_info', []):
            address = ipaddress.IPv4Address(item['local'])
            if address.is_loopback or address.is_link_local or address.is_multicast:
                continue
            # The iPhone is on Wi-Fi: prefer that interface over Ethernet/VPNs.
            priority = 0 if name.startswith(('wlan', 'wl')) else 1 if name.startswith(('eth', 'en')) else 2
            candidates.append((priority, name, str(address)))
    return sorted(candidates)[0][2] if candidates else None


def replace_file(path, contents, mode=0o600):
    path = Path(path)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(contents)
    temporary.chmod(mode)
    os.replace(temporary, path)


def ensure_certificate(root, address):
    cert, ca = root / 'server.crt', root / 'ca.crt'
    try:
        run('openssl', 'verify', '-purpose', 'sslserver', '-verify_ip', address,
            '-CAfile', str(ca), str(cert))
        run('openssl', 'verify', '-verify_hostname', 'kite.local', '-CAfile', str(ca), str(cert))
        run('openssl', 'x509', '-in', str(cert), '-noout', '-checkend', '604800')
        return
    except subprocess.CalledProcessError:
        pass
    with tempfile.TemporaryDirectory(prefix='.network-', dir=root) as tmp:
        tmp = Path(tmp)
        ext = tmp / 'server.ext'
        ext.write_text('basicConstraints=critical,CA:FALSE\n'
                       'keyUsage=critical,digitalSignature,keyEncipherment\n'
                       'extendedKeyUsage=serverAuth\n'
                       f'subjectAltName=DNS:kite.local,IP:{address}\n'
                       'subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
        run('openssl', 'req', '-new', '-key', str(root / 'server.key'), '-sha256',
            '-subj', '/CN=Kiwi camera', '-out', str(tmp / 'server.csr'))
        run('openssl', 'x509', '-req', '-in', str(tmp / 'server.csr'),
            '-CA', str(ca), '-CAkey', str(root / 'ca.key'), '-set_serial',
            '0x' + secrets.token_hex(16), '-days', '365', '-sha256',
            '-extfile', str(ext), '-out', str(tmp / 'server.crt'))
        run('openssl', 'verify', '-purpose', 'sslserver', '-verify_ip', address,
            '-CAfile', str(ca), str(tmp / 'server.crt'))
        run('openssl', 'verify', '-verify_hostname', 'kite.local', '-CAfile', str(ca),
            str(tmp / 'server.crt'))
        shutil.copy2(cert, root / 'server.before-network.crt')
        replace_file(cert, (tmp / 'server.crt').read_text(), 0o644)


def prepare(home):
    root = home / 'kiwi-https-pruebas'
    config_path = home / 'kiwi-pruebas/pi/config.yaml'
    config = yaml.safe_load(config_path.read_text())
    address = None
    for _ in range(30):
        try:
            address = find_address()
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        if address:
            break
        time.sleep(1)
    if not address:
        # Still let the local station open so the user can fix Wi-Fi.
        print('Wi-Fi sin dirección: se conserva la última conexión de cámara.', flush=True)
        return
    ensure_certificate(root, address)
    url = f'https://{address}:8000'
    runtime = home / 'kiwi-tools/runtime'
    qrencode = runtime / 'usr/bin/qrencode'
    environment = os.environ.copy()
    environment['LD_LIBRARY_PATH'] = str(runtime / 'usr/lib/arm-linux-gnueabihf')
    with tempfile.TemporaryDirectory(prefix='.qr-', dir=root) as tmp:
        qr = Path(tmp) / 'camera.png'
        run(str(qrencode) if qrencode.exists() else 'qrencode', '-t', 'PNG', '-s', '6',
            '-m', '4', '-o', str(qr), url + '/camera', env=environment)
        for target in (root / 'phone-camera-qr.png', home / 'kiwi-pruebas/pi/kiwi_pi/static/phone-qr.png'):
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.new')
            shutil.copyfile(qr, temporary)
            temporary.chmod(0o644)
            os.replace(temporary, target)
    config.setdefault('server', {})['public_url'] = url
    replace_file(config_path, yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    overlay = root / 'server-config.yaml'
    if overlay.exists():
        values = yaml.safe_load(overlay.read_text()) or {}
        values.setdefault('server', {})['public_url'] = url
        replace_file(overlay, yaml.safe_dump(values, allow_unicode=True, sort_keys=False))
    replace_file(root / 'camera-url.txt', url + '/camera\n', 0o644)
    print('Cámara lista: ' + url + '/camera', flush=True)


if __name__ == '__main__':
    prepare(Path.home())
