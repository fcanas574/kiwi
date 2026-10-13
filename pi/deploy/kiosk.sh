#!/usr/bin/env bash
# Abre la URL configurada después de comprobar el servicio con su CA local.
set -euo pipefail

PI_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="$PI_DIR/config.yaml"
[[ -f "$CONFIG_FILE" ]] || CONFIG_FILE="$PI_DIR/config.example.yaml"
PYTHON="$PI_DIR/.venv/bin/python"
[[ -x "$PYTHON" ]] || { printf 'No existe el entorno de Python en %s.\n' "$PYTHON" >&2; exit 1; }

SERVER_CONFIG=$("$PYTHON" - "$CONFIG_FILE" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding='utf-8') as config_file:
    server = (yaml.safe_load(config_file) or {}).get('server', {})
print(server.get('public_url') or '')
print(server.get('tls_certfile') or '')
print(server.get('tls_keyfile') or '')
PY
) || { printf 'No se pudo leer la configuración del kiosco: %s\n' "$CONFIG_FILE" >&2; exit 1; }
mapfile -t SERVER_SETTINGS <<< "$SERVER_CONFIG"
PUBLIC_URL="${SERVER_SETTINGS[0]:-}"
TLS_CERTFILE="${SERVER_SETTINGS[1]:-}"
TLS_KEYFILE="${SERVER_SETTINGS[2]:-}"

if [[ -n "$TLS_CERTFILE" || -n "$TLS_KEYFILE" ]]; then
  [[ -n "$TLS_CERTFILE" && -n "$TLS_KEYFILE" ]] || {
    printf 'server.tls_certfile y server.tls_keyfile deben configurarse juntos.\n' >&2
    exit 1
  }
  [[ "$PUBLIC_URL" == https://* ]] || {
    printf 'Con TLS, configura server.public_url con la URL HTTPS de la IP del certificado.\n' >&2
    exit 1
  }
  CA_CERTFILE="$(dirname "$TLS_CERTFILE")/ca.crt"
  [[ -r "$CA_CERTFILE" ]] || {
    printf 'No se puede leer la CA local para el kiosco: %s\n' "$CA_CERTFILE" >&2
    exit 1
  }
  curl_health() {
    curl --connect-timeout 3 --max-time 5 --cacert "$CA_CERTFILE" -fsS \
      "$PUBLIC_URL/api/status" >/dev/null 2>&1
  }
else
  [[ "$PUBLIC_URL" != https://* ]] || {
    printf 'server.public_url usa HTTPS, pero no hay certificado TLS configurado.\n' >&2
    exit 1
  }
  if [[ -z "$PUBLIC_URL" ]]; then
    PUBLIC_URL='http://localhost:8000'
  fi
  [[ "$PUBLIC_URL" == http://* ]] || {
    printf 'server.public_url debe usar http:// o https://.\n' >&2
    exit 1
  }
  curl_health() {
    curl --connect-timeout 3 --max-time 5 -fsS "$PUBLIC_URL/api/status" >/dev/null 2>&1
  }
fi

READY=0
for _ in $(seq 1 60); do
  if curl_health; then
    READY=1
    break
  fi
  sleep 1
done
[[ "$READY" == 1 ]] || {
  printf 'El servicio Kiwi no respondió en %s/api/status.\n' "$PUBLIC_URL" >&2
  exit 1
}

command -v unclutter >/dev/null && unclutter -idle 1 &
BROWSER=$(command -v chromium-browser || command -v chromium)
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
  --disable-features=Translate --overscroll-history-navigation=0 --disable-pinch \
  --check-for-update-interval=31536000 --app="$PUBLIC_URL"
