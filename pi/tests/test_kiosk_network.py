"""Exercise the kiosk's real TLS transport without a Pi or installed CA."""
from __future__ import annotations

import json
import base64
import hashlib
import os
import shutil
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
import yaml

from kiwi_pi.__main__ import MOCK
from kiwi_pi.app import create_app
from kiwi_pi.config import load_config
from test_https_config import run_setup


@pytest.fixture
def kiosk_service(tmp_path, monkeypatch):
    # Discover the routed local interface without sending any datagram.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        try:
            route.connect(('192.0.2.1', 9))
            ip = route.getsockname()[0]
        except OSError:
            pytest.skip('A non-loopback IPv4 interface is required')
    if ip.startswith('127.'):
        pytest.skip('A non-loopback IPv4 interface is required')
    cert_dir = tmp_path / 'certificates'
    result = run_setup(tmp_path, cert_dir, ip=ip)
    assert result.returncode == 0, result.stderr
    monkeypatch.setenv('KIWI_DATA_DIR', str(tmp_path / 'data'))
    cfg = load_config(overrides={**MOCK, 'server': {'allow_remote_admin': False}})
    app = create_app(cfg)
    peers = []

    @app.middleware('http')
    async def record_peer(request, call_next):
        peers.append((request.url.path, request.client.host))
        return await call_next(request)

    listener = socket.socket()
    listener.bind(('0.0.0.0', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, log_level='error', ssl_certfile=str(cert_dir / 'server.crt'),
        ssl_keyfile=str(cert_dir / 'server.key'), proxy_headers=False,
    ))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started, 'Temporary TLS mock service did not start'
    pi_dir = tmp_path / 'pi'
    (pi_dir / 'deploy').mkdir(parents=True)
    (pi_dir / '.venv' / 'bin').mkdir(parents=True)
    python = pi_dir / '.venv' / 'bin' / 'python'
    python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    python.chmod(0o700)
    script = pi_dir / 'deploy' / 'kiosk.sh'
    shutil.copy(Path(__file__).parents[1] / 'deploy' / 'kiosk.sh', script)
    url = f'https://{ip}:{port}'
    (pi_dir / 'config.yaml').write_text(yaml.safe_dump({'server': {
        'public_url': url, 'tls_certfile': str(cert_dir / 'server.crt'),
        'tls_keyfile': str(cert_dir / 'server.key'),
    }}))
    bin_dir = tmp_path / 'kiosk-bin'
    bin_dir.mkdir()
    capture = tmp_path / 'browser-args'
    browser = bin_dir / 'chromium-browser'
    browser.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$KIOSK_CAPTURE"\n')
    browser.chmod(0o700)
    env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}',
           'KIOSK_CAPTURE': str(capture)}
    try:
        yield script, env, capture, url, cert_dir, peers
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), 'Temporary server did not stop'


def curl(url, cert_dir, *options):
    result = subprocess.run([
        'curl', '--noproxy', '*', '--connect-timeout', '2', '--max-time', '5',
        '--cacert', str(cert_dir / 'ca.crt'), *options, url,
    ], capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr
    return result.stdout


def launch_kiosk(service):
    script, env, capture, *_ = service
    result = subprocess.run(['bash', str(script)], env=env, capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return capture.read_text().splitlines()


def test_kiosk_readiness_uses_loopback_and_remote_admin_stays_denied(kiosk_service):
    _, _, _, url, cert_dir, peers = kiosk_service
    args = launch_kiosk(kiosk_service)
    assert ('/api/status', '127.0.0.1') in peers
    assert f'--app={url}' in args
    assert '--no-proxy-server' in args
    assert any(arg.startswith('--host-resolver-rules=') for arg in args)
    ip_port = url.removeprefix('https://')
    local = json.loads(curl(url + '/api/status', cert_dir,
                            '--connect-to', f'{ip_port}:127.0.0.1:{ip_port.split(":")[1]}'))
    assert local['admin'] is True
    assert curl(url + '/api/leds/off', cert_dir, '--request', 'POST',
                '--connect-to', f'{ip_port}:127.0.0.1:{ip_port.split(":")[1]}',
                '--write-out', '%{http_code}').endswith('200')
    remote = json.loads(curl(url + '/api/status', cert_dir))
    assert remote['admin'] is False
    assert peers[-1][1] == ip_port.split(':')[0]
    assert curl(url + '/api/leds/off', cert_dir, '--request', 'POST',
                '--write-out', '%{http_code}').endswith('403')


def test_real_chromium_kiosk_ip_mapping_uses_local_admin(kiosk_service, tmp_path):
    chrome = os.environ.get('KIWI_TEST_CHROME')
    if not chrome:
        pytest.skip('Set KIWI_TEST_CHROME for isolated headless browser validation')
    _, _, _, url, cert_dir, peers = kiosk_service
    args = launch_kiosk(kiosk_service)
    # Trust only the disposable leaf's SPKI in this temporary browser process.
    # This verifies transport and origin, not device/system CA trust.
    public_key = subprocess.run(['openssl', 'x509', '-in', str(cert_dir / 'server.crt'),
                                 '-pubkey', '-noout'], capture_output=True, check=True).stdout
    der = subprocess.run(['openssl', 'pkey', '-pubin', '-outform', 'DER'],
                         input=public_key, capture_output=True, check=True).stdout
    spki = base64.b64encode(hashlib.sha256(der).digest()).decode()
    transport_args = [arg for arg in args if arg.startswith('--host-resolver-rules=')
                      or arg == '--no-proxy-server']
    peers.clear()
    result = subprocess.run([chrome, '--headless', '--disable-gpu', '--no-first-run',
                             '--disable-background-networking',
                             f'--user-data-dir={tmp_path / "browser-profile"}',
                             f'--ignore-certificate-errors-spki-list={spki}',
                             *transport_args, '--dump-dom', url + '/api/status'],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert '"admin":true' in result.stdout, result.stdout
    assert ('/api/status', '127.0.0.1') in peers, peers


def test_kiosk_resolves_relative_tls_paths_from_service_directory(kiosk_service):
    script, _, _, _, _, peers = kiosk_service
    config_file = script.parent.parent / 'config.yaml'
    config = yaml.safe_load(config_file.read_text())
    config['server']['tls_certfile'] = '../certificates/server.crt'
    config['server']['tls_keyfile'] = '../certificates/server.key'
    config_file.write_text(yaml.safe_dump(config))
    launch_kiosk(kiosk_service)
    assert ('/api/status', '127.0.0.1') in peers


@pytest.mark.parametrize('missing', ['ca.crt', 'server.crt', 'server.key'])
def test_kiosk_rejects_missing_tls_material_before_browser_launch(kiosk_service, missing):
    script, env, capture, _, cert_dir, _ = kiosk_service
    (cert_dir / missing).unlink()
    result = subprocess.run(['bash', str(script)], env=env, capture_output=True,
                            text=True, timeout=5)
    assert result.returncode != 0
    assert not capture.exists()
    assert 'leer' in result.stderr


def test_kiosk_readiness_rejects_unrelated_ca(kiosk_service, tmp_path):
    script, env, capture, _, cert_dir, peers = kiosk_service
    other = tmp_path / 'unrelated-ca'
    result = run_setup(tmp_path, other)
    assert result.returncode == 0, result.stderr
    shutil.copy(other / 'ca.crt', cert_dir / 'ca.crt')
    # End the retry loop after its first failed real curl request.
    sleep = Path(env['PATH'].split(':')[0]) / 'sleep'
    sleep.write_text('#!/bin/sh\nexit 1\n')
    sleep.chmod(0o700)
    result = subprocess.run(['bash', str(script)], env=env, capture_output=True,
                            text=True, timeout=8)
    assert result.returncode != 0
    assert not capture.exists()
    assert not peers  # TLS validation fails before an HTTP request is sent.


def test_kiosk_http_development_keeps_default_url(kiosk_service):
    script, env, _, *_ = kiosk_service
    (script.parent.parent / 'config.yaml').write_text('server: {}\n')
    curl_stub = Path(env['PATH'].split(':')[0]) / 'curl'
    curl_stub.write_text('#!/bin/sh\nexit 0\n')
    curl_stub.chmod(0o700)
    args = launch_kiosk(kiosk_service)
    assert '--app=http://localhost:8000' in args
    assert not any(arg.startswith('--host-resolver-rules=') for arg in args)
