from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import kiwi_pi.__main__ as kiwi_main


SETUP_SCRIPT = Path(__file__).parents[1] / 'deploy' / 'setup-local-https.sh'


class ServerConfig:
    def __init__(self, values: dict):
        self.values = values

    def section(self, name: str) -> dict:
        return self.values if name == 'server' else {}


def run_main(monkeypatch: pytest.MonkeyPatch, server: dict) -> dict:
    captured: dict = {}
    app = object()
    monkeypatch.setattr(kiwi_main, 'load_config', lambda *_args, **_kwargs: ServerConfig(server))
    monkeypatch.setattr(kiwi_main, 'create_app', lambda _cfg: app)
    monkeypatch.setattr(kiwi_main.uvicorn, 'run', lambda _app, **kwargs: captured.update(**kwargs))
    monkeypatch.setattr(sys, 'argv', ['kiwi_pi'])
    kiwi_main.main()
    return captured


def make_qrencode_stub(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / f'bin-{len(list(tmp_path.glob("bin-*")))}'
    bin_dir.mkdir()
    capture = tmp_path / 'qrencode-args.txt'
    executable = bin_dir / 'qrencode'
    executable.write_text(
        '#!/bin/sh\n'
        'out=\n'
        'previous=\n'
        'for argument in "$@"; do\n'
        '  if [ "$previous" = "-o" ]; then out="$argument"; fi\n'
        '  previous="$argument"\n'
        'done\n'
        'printf "%s\\n" "$*" > "$QR_ENCODE_CAPTURE"\n'
        'printf "test QR image\\n" > "$out"\n'
    )
    executable.chmod(0o700)
    return bin_dir, capture


def run_setup(tmp_path: Path, output: Path, ip: str = '192.168.1.20', ca_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    bin_dir, capture = make_qrencode_stub(tmp_path)
    env = os.environ.copy()
    env['PATH'] = f'{bin_dir}{os.pathsep}{env.get("PATH", "")}'
    env['QR_ENCODE_CAPTURE'] = str(capture)
    args = ['bash', str(SETUP_SCRIPT), '--ip', ip, '--output', str(output)]
    if ca_dir is not None:
        args.extend(['--ca-dir', str(ca_dir)])
    return subprocess.run(args, env=env, text=True, capture_output=True, check=False)


def openssl(*args: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which('openssl')
    assert executable, 'OpenSSL is required for the local HTTPS certificate test'
    return subprocess.run([executable, *args], text=True, capture_output=True, check=False)


def test_main_keeps_http_when_tls_paths_are_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = run_main(monkeypatch, {
        'host': '0.0.0.0', 'port': 8000, 'tls_certfile': '', 'tls_keyfile': '', 'public_url': '',
    })

    assert captured == {'host': '0.0.0.0', 'port': 8000, 'log_level': 'warning'}


def test_main_passes_both_tls_paths_to_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    cert = '/private/kiwi/server.crt'
    key = '/private/kiwi/server.key'
    captured = run_main(monkeypatch, {
        'host': '0.0.0.0', 'port': 8000, 'tls_certfile': cert, 'tls_keyfile': key,
        'public_url': 'https://192.168.1.20:8000',
    })

    assert captured['ssl_certfile'] == cert
    assert captured['ssl_keyfile'] == key


@pytest.mark.parametrize('server', [
    {'tls_certfile': '/private/kiwi/server.crt', 'tls_keyfile': ''},
    {'tls_certfile': '', 'tls_keyfile': '/private/kiwi/server.key'},
])
def test_main_rejects_incomplete_tls_configuration(monkeypatch: pytest.MonkeyPatch, server: dict) -> None:
    with pytest.raises(ValueError, match=r'server\.tls_certfile y server\.tls_keyfile deben configurarse juntos'):
        run_main(monkeypatch, server)


def test_setup_script_creates_trusted_ip_certificate_and_secure_keys(tmp_path: Path) -> None:
    output = tmp_path / 'private-https'
    result = run_setup(tmp_path, output)

    assert result.returncode == 0, result.stderr
    ca_cert = output / 'ca.crt'
    ca_der = output / 'ca.cer'
    server_cert = output / 'server.crt'
    server_key = output / 'server.key'
    ca_key = output / 'ca.key'
    verification = openssl('verify', '-CAfile', str(ca_cert), str(server_cert))
    assert verification.returncode == 0, verification.stderr

    san = openssl('x509', '-in', str(server_cert), '-noout', '-ext', 'subjectAltName')
    assert san.returncode == 0
    assert 'IP Address:192.168.1.20' in san.stdout
    eku = openssl('x509', '-in', str(server_cert), '-noout', '-ext', 'extendedKeyUsage')
    assert eku.returncode == 0
    assert 'TLS Web Server Authentication' in eku.stdout
    assert server_key.stat().st_mode & 0o077 == 0
    assert ca_key.stat().st_mode & 0o077 == 0
    assert openssl('x509', '-inform', 'DER', '-in', str(ca_der), '-noout').returncode == 0

    config = yaml.safe_load((output / 'server-config.yaml').read_text())
    configured_server = config['server']
    assert configured_server['public_url'] == 'https://192.168.1.20:8000'
    assert configured_server['tls_certfile'] == str(server_cert)
    assert configured_server['tls_keyfile'] == str(server_key)
    qr_capture = (tmp_path / 'qrencode-args.txt').read_text()
    assert 'https://192.168.1.20:8000' in qr_capture
    assert 'KIWI_API_KEY' not in qr_capture


def test_setup_script_can_reuse_ca_for_a_new_server_certificate(tmp_path: Path) -> None:
    ca_dir = tmp_path / 'initial-https'
    first = run_setup(tmp_path, ca_dir)
    assert first.returncode == 0, first.stderr
    original_ca = (ca_dir / 'ca.crt').read_bytes()

    renewed_dir = tmp_path / 'renewed-server'
    renewed = run_setup(tmp_path, renewed_dir, ip='192.168.1.21', ca_dir=ca_dir)

    assert renewed.returncode == 0, renewed.stderr
    assert (renewed_dir / 'ca.crt').read_bytes() == original_ca
    assert openssl('x509', '-inform', 'DER', '-in', str(renewed_dir / 'ca.cer'), '-noout').returncode == 0
    assert not (renewed_dir / 'ca.key').exists()
    verification = openssl('verify', '-CAfile', str(renewed_dir / 'ca.crt'), str(renewed_dir / 'server.crt'))
    assert verification.returncode == 0, verification.stderr
    san = openssl('x509', '-in', str(renewed_dir / 'server.crt'), '-noout', '-ext', 'subjectAltName')
    assert 'IP Address:192.168.1.21' in san.stdout


def test_setup_script_rejects_invalid_ip_and_existing_output_without_overwrite(tmp_path: Path) -> None:
    invalid_output = tmp_path / 'invalid-ip-output'
    invalid = run_setup(tmp_path, invalid_output, ip='192.168.1.999')
    assert invalid.returncode != 0
    assert 'IP' in invalid.stderr
    assert not invalid_output.exists()

    output = tmp_path / 'private-https'
    created = run_setup(tmp_path, output)
    assert created.returncode == 0, created.stderr
    original_key = (output / 'server.key').read_bytes()
    rerun = run_setup(tmp_path, output)
    assert rerun.returncode != 0
    assert 'ya existe' in rerun.stderr
    assert (output / 'server.key').read_bytes() == original_key
