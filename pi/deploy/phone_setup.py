#!/usr/bin/env python3
"""Public certificate setup page. Does not serve directory contents or keys."""
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path.home() / 'kiwi-https-pruebas'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?')[0] == '/':
            url = (ROOT / 'camera-url.txt').read_text().strip()
            payload = ('''<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Kiwi · conectar iPhone</title>
<style>body{font:17px system-ui;margin:30px auto;padding:20px;max-width:600px;color:#111;background:#f4f6fa}
a{color:#006dff}li{margin:15px 0}h1{font-size:28px}</style><h1>Conecta la cámara del iPhone</h1>
<p>Si ya instalaste el certificado y activaste su confianza, abre directamente la cámara.</p>
<p><a href="''' + escape(url, quote=True) + '''">Abrir cámara Kiwi →</a></p>
<ol><li><a href="/kiwi.mobileconfig">Descargar perfil de certificado Kiwi</a> y toca «Permitir».</li>
<li>Abre Ajustes → General → VPN y gestión de dispositivos → perfil Kiwi → Instalar.</li>
<li>Abre Ajustes → General → Información → Ajustes de confianza de los certificados y activa la confianza de Kiwi.</li>
<li>Regresa a Safari y abre la cámara. El código se muestra en el botón «Cámara» de la Raspberry.</li></ol>
<p>El iPhone y la Raspberry deben estar en el mismo Wi-Fi.</p></html>''').encode()
            content_type = 'text/html; charset=utf-8'
        elif self.path == '/kiwi.mobileconfig':
            payload = (ROOT / 'kiwi.mobileconfig').read_bytes()
            content_type = 'application/x-apple-aspen-config'
        elif self.path == '/kiwi-iphone-ca.cer':
            payload = (ROOT / 'ca.cer').read_bytes()
            content_type = 'application/pkix-cert'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8081), Handler).serve_forever()
