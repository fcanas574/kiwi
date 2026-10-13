#!/usr/bin/env bash
# Abre la URL configurada después de comprobar el servicio con su CA local.
set -euo pipefail

PI_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PI_DIR"  # Las rutas TLS relativas usan el mismo directorio que kiwi.service.
CONFIG_FILE="$PI_DIR/config.yaml"
[[ -f "$CONFIG_FILE" ]] || CONFIG_FILE="$PI_DIR/config.example.yaml"
PYTHON="$PI_DIR/.venv/bin/python"
[[ -x "$PYTHON" ]] || { printf 'No existe el entorno de Python en %s.\n' "$PYTHON" >&2; exit 1; }

SERVER_CONFIG=$("$PYTHON" - "$CONFIG_FILE" <<'PY'
import sys
import ipaddress
from urllib.parse import urlsplit
import yaml

with open(sys.argv[1], encoding='utf-8') as config_file:
    server = (yaml.safe_load(config_file) or {}).get('server', {})
print(server.get('public_url') or '')
print(str(server.get('tls_certfile') or '').strip())
print(str(server.get('tls_keyfile') or '').strip())
if server.get('tls_certfile') or server.get('tls_keyfile'):
    url = urlsplit(server.get('public_url') or '')
    if url.scheme != 'https' or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
        raise ValueError('server.public_url debe ser una URL HTTPS de la IP, sin credenciales ni ruta.')
    host = ipaddress.ip_address(url.hostname or '').compressed
    print(f'[{host}]' if ':' in host else host)
    print(url.port or 443)
PY
) || { printf 'No se pudo leer la configuración del kiosco: %s\n' "$CONFIG_FILE" >&2; exit 1; }
SERVER_SETTINGS=()
while IFS= read -r setting; do
  SERVER_SETTINGS+=("$setting")
done <<< "$SERVER_CONFIG"
PUBLIC_URL="${SERVER_SETTINGS[0]:-}"
TLS_CERTFILE="${SERVER_SETTINGS[1]:-}"
TLS_KEYFILE="${SERVER_SETTINGS[2]:-}"
BROWSER_TRANSPORT=()

if [[ -n "$TLS_CERTFILE" || -n "$TLS_KEYFILE" ]]; then
  [[ -n "$TLS_CERTFILE" && -n "$TLS_KEYFILE" ]] || {
    printf 'server.tls_certfile y server.tls_keyfile deben configurarse juntos.\n' >&2
    exit 1
  }
  [[ -r "$TLS_CERTFILE" && -r "$TLS_KEYFILE" ]] || {
    printf 'No se pueden leer el certificado o la clave TLS configurados.\n' >&2
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
  URL_HOST="${SERVER_SETTINGS[3]}"
  URL_PORT="${SERVER_SETTINGS[4]}"
  # Conserva el origen/IP del certificado, pero solo el kiosco conecta por loopback.
  # Desactiva proxies para que no conviertan la petición local en una petición LAN.
  BROWSER_TRANSPORT=(--no-proxy-server "--host-resolver-rules=MAP $URL_HOST 127.0.0.1")
  curl_health() {
    curl --noproxy '*' --connect-timeout 3 --max-time 5 --cacert "$CA_CERTFILE" -fsS \
      --connect-to "$URL_HOST:$URL_PORT:127.0.0.1:$URL_PORT" \
      "${PUBLIC_URL%/}/api/status" >/dev/null 2>&1
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
  --check-for-update-interval=31536000 ${BROWSER_TRANSPORT[@]+"${BROWSER_TRANSPORT[@]}"} --app="$PUBLIC_URL"
