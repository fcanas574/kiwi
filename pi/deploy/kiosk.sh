#!/usr/bin/env bash
# Abre la interfaz táctil a pantalla completa cuando el servidor responde.
for _ in $(seq 1 60); do
  curl -sf http://localhost:8000/api/status >/dev/null && break
  sleep 1
done
command -v unclutter >/dev/null && unclutter -idle 1 &
BROWSER=$(command -v chromium-browser || command -v chromium)
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
  --disable-features=Translate --overscroll-history-navigation=0 --disable-pinch \
  --check-for-update-interval=31536000 --app=http://localhost:8000
