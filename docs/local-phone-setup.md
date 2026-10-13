# HTTPS local para la cámara del teléfono

La cámara del navegador requiere abrir Kiwi desde un contexto HTTPS confiable. La Pi sirve la página y reenvía cada captura a Colab; no descarga modelos. Esta configuración conserva HTTP cuando `server.tls_certfile` y `server.tls_keyfile` están vacíos, para desarrollo.

## Reservar la dirección y emitir certificados

En el router, crea una reserva DHCP para la Raspberry Pi y anota su IP, por ejemplo `192.168.1.20`. Usa una dirección que permanezca fija en la Wi-Fi de la demostración. El certificado se emite para esa IP; abrir `localhost`, un nombre `.local` u otra dirección dará un error de nombre aunque se haya instalado la CA.

En la Pi, ejecuta el generador con una carpeta privada nueva fuera del checkout de Git:

```bash
cd ~/kiwi/pi
bash deploy/setup-local-https.sh --ip 192.168.1.20 --output "$HOME/kiwi-https-2026"
```

El script usa el OpenSSL del sistema, crea una CA y un certificado de servidor de 365 días, valida que el certificado corresponda a la IP y a `serverAuth`, y crea un QR local con `https://192.168.1.20:8000`. La carpeta queda con permisos `700`; las claves privadas, con `600`. El QR solo contiene la URL, sin credenciales. No copies `ca.key` ni `server.key` al teléfono ni a otro equipo.

El script se niega a usar una carpeta de salida que ya exista. Guarda `ca.crt`, `ca.cer`, `server.crt`, `server.key`, `phone-camera-qr.png` y `server-config.yaml`. Copia la sección `server` de `server-config.yaml` a `pi/config.yaml`, conservando el resto de esa sección, y reinicia el servicio:

```bash
sudo systemctl restart kiwi
sudo systemctl status kiwi
```

La sección configura `tls_certfile`, `tls_keyfile` y `public_url`. El kiosco usa esa misma URL y comprueba el servicio con `ca.crt`; la comprobación TLS no omite errores de certificado. Para el teléfono, transfiere únicamente el certificado público de la CA: usa `ca.crt` en Android y `ca.cer` en iOS. Confirma su huella SHA-256 en la Pi antes de instalarlo:

```bash
openssl x509 -in "$HOME/kiwi-https-2026/ca.crt" -noout -fingerprint -sha256
openssl x509 -inform DER -in "$HOME/kiwi-https-2026/ca.cer" -noout -fingerprint -sha256
```

Las dos huellas deben coincidir. Transfiere el archivo por cable, AirDrop u otro medio local que controles. El generador no descarga ni instala certificados en el teléfono.

## Confiar la CA en Android

Los nombres de menú varían entre fabricantes y versiones. En Pixel, la ruta de certificados comienza en *Settings → Security & privacy → More security settings → Encryption & credentials → Install a certificate*; la guía de Pixel describe algunos pasos como disponibles solo en Android 14 o posterior. Para HTTPS de Kiwi, selecciona el tipo **CA certificate** o la opción equivalente de certificado raíz del dispositivo. No selecciones **Wi-Fi certificate**: esa opción configura credenciales para una red empresarial, no la confianza HTTPS de Kiwi. AOSP separa explícitamente certificados CA y Wi-Fi y dirige la instalación de CA a Settings. El dispositivo puede pedir que configures un PIN o bloqueo de pantalla.

Si la opción para instalar una CA de usuario no aparece, consulta las instrucciones del fabricante y versión concretos. No uses una opción de Wi-Fi ni continúes solo porque el navegador permita ignorar una advertencia.

## Confiar la CA en iPhone

Envía `ca.cer` al iPhone, ábrelo y sigue el flujo de instalación del perfil/certificado. Luego ve a *Settings → General → About → Certificate Trust Settings* y activa **Enable full trust for root certificates** para la CA de Kiwi. Una instalación manual no habilita por sí sola la confianza SSL/TLS; la confianza completa es un paso aparte según Apple.

## Confiar la CA en el kiosco de la Pi

Instala la CA en el almacén de certificados del sistema y en el perfil NSS del usuario que inicia Chromium. El instalador de Kiwi incluye `libnss3-tools` para `certutil`:

```bash
sudo install -m 0644 "$HOME/kiwi-https-2026/ca.crt" /usr/local/share/ca-certificates/kiwi-local-ca.crt
sudo update-ca-certificates

mkdir -p "$HOME/.pki/nssdb"
if [ ! -f "$HOME/.pki/nssdb/cert9.db" ]; then
  certutil -N -d "sql:$HOME/.pki/nssdb" --empty-password
fi
certutil -D -d "sql:$HOME/.pki/nssdb" -n "Kiwi local HTTPS" 2>/dev/null || true
certutil -A -d "sql:$HOME/.pki/nssdb" -n "Kiwi local HTTPS" -t "C,," -i "$HOME/kiwi-https-2026/ca.crt"
```

`C,,` marca la CA para TLS de sitios web en NSS. Ejecuta `certutil` como el mismo usuario de escritorio que inicia el kiosco. El kiosco comprueba que el servicio responda usando el archivo CA junto a `server.crt` y luego abre `server.public_url`; Chromium también debe confiar en la CA desde su perfil para mostrar la página sin advertencias.

## Abrir la cámara y comprobarla

Con el teléfono y la Pi en la misma Wi-Fi, escanea `phone-camera-qr.png` o abre `https://192.168.1.20:8000` directamente. Confirma que el navegador no muestre advertencias del certificado, permite el acceso a la cámara, mantén Kiwi en primer plano, selecciona la cámara trasera y toma una foto real. Confirma que la página reciba la imagen. Omitir una advertencia no comprueba la confianza. La URL y el certificado deben usar la misma IP reservada.

Después de cambiar la reserva DHCP, emite un certificado de servidor para la nueva IP, actualiza `public_url` y reinicia `kiwi`. El certificado nuevo debe tener un IP SAN para esa dirección.

## Renovar sin perder la confianza

La CA y el certificado del servidor vencen después de 365 días. Puedes consultar sus fechas con:

```bash
openssl x509 -in "$HOME/kiwi-https-2026/ca.crt" -noout -enddate
openssl x509 -in "$HOME/kiwi-https-2026/server.crt" -noout -enddate
```

Para renovar solo el certificado del servidor mientras la CA siga vigente, elige una carpeta de salida nueva y pasa la carpeta original de la CA. Así se conserva la misma CA de confianza y no hay que instalarla de nuevo en el teléfono:

```bash
bash deploy/setup-local-https.sh \
  --ip 192.168.1.20 \
  --output "$HOME/kiwi-https-server-renewal" \
  --ca-dir "$HOME/kiwi-https-2026"
```

Combina el nuevo `server-config.yaml` con `pi/config.yaml` y reinicia `kiwi`; el bloque apunta al nuevo `server.crt` y `server.key`. El certificado renovado dura 365 días, pero no puede seguir siendo válido después del vencimiento de la CA. Revisa ambas fechas y renueva antes de que venza la CA.

Para reemplazar una CA vencida o rotarla, omite `--ca-dir` y crea una CA nueva en otra carpeta. Después, instala y confía en la CA nueva desde cada teléfono y desde el kiosco; la confianza anterior no se transfiere. Guarda una copia segura de la clave privada de la CA si planeas reutilizarla.

Referencias principales: [configuración HTTPS de Uvicorn](https://www.uvicorn.org/settings/), [distribución de certificados de Apple](https://support.apple.com/en-us/guide/deployment/depcdc9a6a3f/web), [confianza SSL manual de Apple](https://support.apple.com/en-us/102390), [ayuda de certificados para Pixel](https://support.google.com/pixelphone/answer/2844832), [instalador de certificados de AOSP](https://android.googlesource.com/platform/packages/apps/CertInstaller.git/+/8f786d61c2b589e09b0dd242d6aec4d3b6a360f5/res/values-en-rCA/strings.xml) y [confianza de sitios web en NSS](https://firefox-source-docs.mozilla.org/security/nss/runbooks/rootstore.html).
