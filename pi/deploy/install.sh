#!/usr/bin/env bash
# Instala la estación Kiwi en una Raspberry Pi 4 con Raspberry Pi OS (Bookworm, 64 bits, escritorio).
# Uso: cd ~/kiwi/pi && bash deploy/install.sh
set -euo pipefail

PI_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
cd "$PI_DIR"

echo "==> Paquetes del sistema (cámara, GPIO, navegador del kiosco)"
sudo apt-get update
sudo apt-get install -y python3-venv python3-picamera2 python3-gpiozero python3-lgpio chromium-browser unclutter || \
sudo apt-get install -y python3-venv python3-picamera2 python3-gpiozero python3-lgpio chromium unclutter

echo "==> Entorno virtual (con acceso a picamera2 y gpiozero del sistema)"
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "==> Se creó config.yaml: ajusta drivers, pines y herramientas."
fi

if [ ! -f .env ]; then
  cat > .env <<'EOF'
# Clave de la API REST de Colab (la misma del secreto KIWI_API_KEY del notebook)
KIWI_API_KEY=
EOF
  chmod 600 .env
fi

echo "==> Servicio systemd"
sed -e "s|__PI_DIR__|$PI_DIR|g" -e "s|__USER__|$USER_NAME|g" deploy/kiwi.service | sudo tee /etc/systemd/system/kiwi.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now kiwi.service

echo "==> Kiosco al iniciar sesión"
mkdir -p "$HOME/.config/autostart"
sed -e "s|__PI_DIR__|$PI_DIR|g" deploy/kiwi-kiosk.desktop > "$HOME/.config/autostart/kiwi-kiosk.desktop"
chmod +x deploy/kiosk.sh

echo
echo "Listo. Estado del servidor: sudo systemctl status kiwi"
echo "Página local: http://localhost:8000  ·  desde el celular: http://$(hostname).local:8000"
echo "Reinicia la Pi para abrir el kiosco automáticamente."
