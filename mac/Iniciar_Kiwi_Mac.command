#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
exec /usr/bin/caffeinate -i .venv/bin/python run.py
