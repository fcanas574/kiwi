#!/usr/bin/env bash
# Crea una CA privada y un certificado HTTPS local para el teléfono dedicado.
set -euo pipefail

usage() {
  cat <<'EOF'
Uso:
  bash setup-local-https.sh --ip <IP-reservada> --output <directorio-nuevo>
  bash setup-local-https.sh --ip <IP-reservada> --output <directorio-nuevo> --ca-dir <directorio-CA-existente>

La segunda forma conserva la CA existente y emite un certificado nuevo para la IP indicada.
EOF
}

fail() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

IP_INPUT=
OUTPUT_INPUT=
CA_DIR_INPUT=
while (($#)); do
  case "$1" in
    --ip)
      (($# >= 2)) || { usage >&2; fail 'falta el valor de --ip'; }
      IP_INPUT=$2
      shift 2
      ;;
    --output)
      (($# >= 2)) || { usage >&2; fail 'falta el valor de --output'; }
      OUTPUT_INPUT=$2
      shift 2
      ;;
    --ca-dir)
      (($# >= 2)) || { usage >&2; fail 'falta el valor de --ca-dir'; }
      CA_DIR_INPUT=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "opción desconocida: $1"
      ;;
  esac
done

[[ -n "$IP_INPUT" ]] || { usage >&2; fail 'se requiere --ip'; }
[[ -n "$OUTPUT_INPUT" ]] || { usage >&2; fail 'se requiere --output'; }
command -v openssl >/dev/null 2>&1 || fail 'no se encontró openssl; instálalo en la Raspberry Pi.'
command -v python3 >/dev/null 2>&1 || fail 'no se encontró python3.'
command -v qrencode >/dev/null 2>&1 || fail 'no se encontró qrencode; en Raspberry Pi OS instala el paquete qrencode.'

IP_DATA=$(python3 - "$IP_INPUT" <<'PY'
import ipaddress
import sys

try:
    address = ipaddress.ip_address(sys.argv[1])
except ValueError:
    print('La dirección indicada en --ip no es una IP válida.', file=sys.stderr)
    raise SystemExit(1)

print(address.compressed)
print(f'[{address.compressed}]' if address.version == 6 else address.compressed)
PY
) || fail 'usa una dirección IPv4 o IPv6 reservada en el router.'
IP=$(printf '%s\n' "$IP_DATA" | sed -n '1p')
URL_HOST=$(printf '%s\n' "$IP_DATA" | sed -n '2p')
PUBLIC_URL="https://${URL_HOST}:8000"

CA_DIR=
CA_CERT=
CA_KEY=
if [[ -n "$CA_DIR_INPUT" ]]; then
  [[ -d "$CA_DIR_INPUT" && ! -L "$CA_DIR_INPUT" ]] || fail '--ca-dir debe ser un directorio real creado para la CA.'
  CA_DIR=$(cd "$CA_DIR_INPUT" && pwd -P)
  CA_CERT="$CA_DIR/ca.crt"
  CA_KEY="$CA_DIR/ca.key"
  [[ -f "$CA_CERT" && ! -L "$CA_CERT" ]] || fail 'no existe ca.crt en --ca-dir.'
  [[ -f "$CA_KEY" && ! -L "$CA_KEY" ]] || fail 'no existe ca.key en --ca-dir; no se puede emitir el certificado.'
  python3 - "$CA_DIR" "$CA_CERT" "$CA_KEY" <<'PY' || fail '--ca-dir y ca.key deben pertenecer al usuario actual y ser privados.'
import os
import stat
import sys

root, cert, key = map(os.stat, sys.argv[1:])
if not stat.S_ISDIR(root.st_mode) or root.st_uid != os.getuid() or root.st_mode & 0o077:
    raise SystemExit(1)
if not stat.S_ISREG(cert.st_mode) or cert.st_uid != os.getuid():
    raise SystemExit(1)
if not stat.S_ISREG(key.st_mode) or key.st_uid != os.getuid() or key.st_mode & 0o077:
    raise SystemExit(1)
PY
  openssl pkey -in "$CA_KEY" -noout -check >/dev/null 2>&1 || fail 'ca.key no es una clave privada válida.'
  openssl x509 -in "$CA_CERT" -noout -checkend 0 >/dev/null 2>&1 || fail 'ca.crt no es válido o ya venció.'
  openssl verify -CAfile "$CA_CERT" "$CA_CERT" >/dev/null 2>&1 || fail 'ca.crt no es una CA autofirmada válida.'
  CA_CONSTRAINTS=$(openssl x509 -in "$CA_CERT" -noout -ext basicConstraints 2>/dev/null || true)
  [[ "$CA_CONSTRAINTS" == *'CA:TRUE'* ]] || fail 'ca.crt no tiene Basic Constraints CA:TRUE.'
fi

OUTPUT_NAME=$(basename "$OUTPUT_INPUT")
OUTPUT_PARENT_INPUT=$(dirname "$OUTPUT_INPUT")
[[ "$OUTPUT_NAME" != '/' && "$OUTPUT_NAME" != '.' && "$OUTPUT_NAME" != '..' ]] || fail '--output debe nombrar un directorio nuevo.'
mkdir -p "$OUTPUT_PARENT_INPUT"
OUTPUT_PARENT=$(cd "$OUTPUT_PARENT_INPUT" && pwd -P)
OUTPUT_DIR="$OUTPUT_PARENT/$OUTPUT_NAME"
[[ ! -e "$OUTPUT_DIR" && ! -L "$OUTPUT_DIR" ]] || fail "ya existe el directorio de salida: $OUTPUT_DIR; elige uno nuevo para evitar sobrescribir archivos."

umask 077
mkdir -m 700 "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR"
OUTPUT_CREATED=1
SUCCESS=0
TMP_DIR=
cleanup() {
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
  if [[ "$SUCCESS" != 1 && "${OUTPUT_CREATED:-0}" == 1 ]]; then
    rm -rf "$OUTPUT_DIR"
  fi
}
trap cleanup EXIT
TMP_DIR=$(mktemp -d "$OUTPUT_DIR/.kiwi-https.XXXXXX")

if [[ -z "$CA_DIR" ]]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes \
    -keyout "$TMP_DIR/ca.key" -out "$TMP_DIR/ca.crt" \
    -subj '/CN=Kiwi local HTTPS CA' \
    -addext 'basicConstraints=critical,CA:TRUE' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -addext 'subjectKeyIdentifier=hash'
  chmod 600 "$TMP_DIR/ca.key"
  CA_CERT="$TMP_DIR/ca.crt"
  CA_KEY="$TMP_DIR/ca.key"
else
  cp "$CA_CERT" "$TMP_DIR/ca.crt"
fi
openssl x509 -in "$TMP_DIR/ca.crt" -outform DER -out "$TMP_DIR/ca.cer"

openssl req -newkey rsa:2048 -nodes -sha256 \
  -keyout "$TMP_DIR/server.key" -out "$TMP_DIR/server.csr" \
  -subj "/CN=$IP"
cat > "$TMP_DIR/server.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:$IP
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
openssl x509 -req -in "$TMP_DIR/server.csr" \
  -CA "$CA_CERT" -CAkey "$CA_KEY" -CAserial "$TMP_DIR/ca.srl" -CAcreateserial \
  -out "$TMP_DIR/server.crt" -days 365 -sha256 -extfile "$TMP_DIR/server.ext"
chmod 600 "$TMP_DIR/server.key"
openssl verify -purpose sslserver -verify_ip "$IP" -CAfile "$TMP_DIR/ca.crt" "$TMP_DIR/server.crt" >/dev/null \
  || fail 'OpenSSL no pudo validar el certificado de servidor, su IP SAN y su uso serverAuth.'

qrencode -t PNG -o "$TMP_DIR/phone-camera-qr.png" "$PUBLIC_URL"
python3 - "$TMP_DIR/server-config.yaml" "$OUTPUT_DIR/server.crt" "$OUTPUT_DIR/server.key" "$PUBLIC_URL" <<'PY'
import json
import pathlib
import sys

path, cert, key, url = sys.argv[1:]
pathlib.Path(path).write_text(
    'server:\n'
    f'  tls_certfile: {json.dumps(cert)}\n'
    f'  tls_keyfile: {json.dumps(key)}\n'
    f'  public_url: {json.dumps(url)}\n'
)
PY

mv "$TMP_DIR/ca.crt" "$OUTPUT_DIR/ca.crt"
mv "$TMP_DIR/ca.cer" "$OUTPUT_DIR/ca.cer"
if [[ -f "$TMP_DIR/ca.key" ]]; then
  mv "$TMP_DIR/ca.key" "$OUTPUT_DIR/ca.key"
fi
mv "$TMP_DIR/server.crt" "$OUTPUT_DIR/server.crt"
mv "$TMP_DIR/server.key" "$OUTPUT_DIR/server.key"
mv "$TMP_DIR/phone-camera-qr.png" "$OUTPUT_DIR/phone-camera-qr.png"
mv "$TMP_DIR/server-config.yaml" "$OUTPUT_DIR/server-config.yaml"
chmod 600 "$OUTPUT_DIR/server.key"
[[ -f "$OUTPUT_DIR/ca.key" ]] && chmod 600 "$OUTPUT_DIR/ca.key"
SUCCESS=1

printf 'Certificado CA: %s\n' "$OUTPUT_DIR/ca.crt"
if [[ -f "$OUTPUT_DIR/ca.key" ]]; then
  printf 'Clave privada de la CA (no compartir): %s\n' "$OUTPUT_DIR/ca.key"
else
  printf 'CA reutilizada; la clave privada permanece en: %s\n' "$CA_KEY"
fi
printf 'Certificado del servidor: %s\n' "$OUTPUT_DIR/server.crt"
printf 'Clave del servidor: %s\n' "$OUTPUT_DIR/server.key"
printf 'QR local: %s\n' "$OUTPUT_DIR/phone-camera-qr.png"
printf 'URL pública local: %s\n' "$PUBLIC_URL"
printf 'Combina la sección server de %s con pi/config.yaml y reinicia kiwi.\n' "$OUTPUT_DIR/server-config.yaml"
