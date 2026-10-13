"""Arranque: `python -m kiwi_pi [--config config.yaml] [--mock | --sim-hardware]`."""
from __future__ import annotations

import argparse

import uvicorn

from .app import create_app
from .config import load_config

MOCK = {
    'recognizer': {'driver': 'mock'},
    'camera': {'driver': 'none'},
    'leds': {'driver': 'mock'},
    'sensors': {'driver': 'mock'},
    'server': {'allow_remote_admin': True},
}

# API real de Colab; LEDs y sensores simulados (probar el reconocimiento antes de tener la Pi).
SIM_HARDWARE = {
    'camera': {'driver': 'none'},
    'leds': {'driver': 'mock'},
    'sensors': {'driver': 'mock'},
}


def main() -> None:
    parser = argparse.ArgumentParser(description='Estación Kiwi para Raspberry Pi')
    parser.add_argument('--config', help='Ruta de config.yaml (por defecto pi/config.yaml)')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--mock', action='store_true', help='Simular API, cámara, LEDs y sensores')
    group.add_argument('--sim-hardware', action='store_true',
                       help='Usar la API real de Colab y simular LEDs y sensores')
    parser.add_argument('--verbose', action='store_true', help='Mostrar cada petición HTTP')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    args = parser.parse_args()

    cfg = load_config(args.config, MOCK if args.mock else SIM_HARDWARE if args.sim_hardware else None)
    server = cfg.section('server')
    tls_certfile = str(server.get('tls_certfile') or '').strip()
    tls_keyfile = str(server.get('tls_keyfile') or '').strip()
    if bool(tls_certfile) != bool(tls_keyfile):
        raise ValueError('server.tls_certfile y server.tls_keyfile deben configurarse juntos.')

    uvicorn_options = {
        'host': args.host or server.get('host', '0.0.0.0'),
        'port': args.port or int(server.get('port', 8000)),
        'log_level': 'info' if args.verbose else 'warning',
    }
    if tls_certfile:
        uvicorn_options.update(ssl_certfile=tls_certfile, ssl_keyfile=tls_keyfile)
    uvicorn.run(create_app(cfg), **uvicorn_options)


if __name__ == '__main__':
    main()
