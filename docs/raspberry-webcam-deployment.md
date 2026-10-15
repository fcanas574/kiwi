> Actualización: el reconocimiento activo se migró al Mac M5 con HTTPS, Qwen MLX y DINO MPS. Ver `mac/README.md` y la guía de encendido. La Pi actual es `10.74.11.201`; el endpoint Mac es `https://10.74.10.134:8003/predict`. Se conservan los LEDs GPIO y los temporizadores de 20 segundos; la cámara sigue en el iPhone. El límite de inicio ahora es 2 cuadros/s con el Mac. Las referencias a Colab y la IP anterior que siguen describen la instalación previa y sus pruebas históricas.

# Instalación de pruebas: Raspberry Pi 4 e iPhone

La instalación activa está en `/home/testuser/kiwi-pruebas/pi`, sobre Raspberry Pi OS 13, usuario `testuser`, pantalla de 800×480 y Raspberry Pi 4 de 4 GB. La IP utilizada es `192.168.58.109`.

## Operación

1. Mantener Colab conectado con Qwen, Grounding DINO y la API de 6b ejecutados. El reconocimiento corre en Colab; la Pi coordina cámara, pantalla y GPIO.
2. Tocar **Cámara** en la Raspberry y abrir en Safari del iPhone el enlace o escanear el QR. La dirección actual es `https://192.168.58.109:8000/camera`. No depender de `kite.local`: Bonjour dejó de resolver en esta red después de las primeras pruebas. El perfil de la CA local debe estar instalado y tener confianza completa. El código de conexión se consulta tocando **Cámara** en la Raspberry. Conectar y permitir cámara; el teléfono solo muestra la captura y su estado de conexión.
3. Dejar Safari visible, mantener fijo el teléfono y colocar el objeto de frente. No superponer instrumentos.
4. Desde la Raspberry, tocar **Iniciar escaneo**. Está seleccionado **Rápido · 1**: presenta una herramienta por vez y exige dos clasificaciones coincidentes en imágenes consecutivas antes de encender el LED. Con el escaneo pausado, el botón permite cambiar a **Varios**, que localiza recortes con DINO y verifica cada seguimiento antes de aceptar.
5. Cada LED se apaga automáticamente **20 segundos después de reconocer su objeto**. La posición muestra la cuenta regresiva y el tiempo de reconocimiento. **Pausar análisis** detiene el reconocimiento; los temporizadores siguen corriendo. **Finalizar y apagar LEDs** apaga todos los LEDs de inmediato, borra el ciclo y permite repetir con el mismo objeto.

Si la cámara pierde conexión durante cuatro segundos, se pausa el escaneo y las respuestas pendientes no pueden activar LEDs. Al reconectar el teléfono, iniciar otra vez desde la Raspberry. Reiniciar el servidor requiere reconectar el teléfono porque las identidades de emparejamiento son temporales.

No hay sensores en esta instalación. El sistema identifica y señala posiciones; no puede comprobar que una herramienta fue físicamente devuelta. Las guías duran 20 segundos por objeto o se cierran antes con **Finalizar y apagar LEDs**. Un objeto que sigue visible no vuelve a encender su LED al vencer el plazo; para repetir inmediatamente, finalizar e iniciar un ciclo nuevo.

## Velocidad y soporte a 45°

El soporte puede apuntar de forma oblicua, aproximadamente a 45°, siempre que el instrumento completo sea visible y no quede tapado. El modelo usa recortes de cámara y no requiere una vista cenital. El rendimiento desde ese soporte debe comprobarse con las imágenes reales.

El modo activo es `scan.mode: single`. Qwen clasifica la escena completa y dos respuestas válidas de la misma categoría confirman el único objeto presentado. No se debe presentar un grupo de herramientas en ese modo: seleccionar **Varios** para reconocer varios recortes.

En **Varios**, la configuración requiere dos detecciones estables (`stable_samples: 2`, `stable_seconds: 0.35`) antes de clasificar, y una imagen posterior compatible antes de encender el LED. La tolerancia de tamaño es 0.3, centro 0.04 e IoU estable 0.5. Se eliminó la espera extra después de cada respuesta: el límite de un inicio de análisis por segundo sigue vigente, y no se acumulan solicitudes simultáneas.

Con la imagen real capturada a aproximadamente 45°, el modo de seguimiento tardó 19.9 segundos en la prueba en vivo. El modo rápido confirmó esa misma imagen en 3.4 segundos y dos cuadros en una prueba con Colab; después el usuario confirmó también **3.4 segundos con la cámara en vivo a 45°**. Los tiempos dependen de la red, Colab y claridad de la escena. El usuario confirmó el apagado físico automático a los 20 segundos tras encenderse el LED.

El plazo automático está en `guide.continuous_led_seconds: 20`; la configuración anterior al cambio se conserva en `pi/config.before-speed-and-20s.yaml`, y la anterior al modo rápido en `pi/config.before-single-mode.yaml`. El modo seleccionado desde la pantalla se guarda en `data/settings.json` como `scan_mode`.

## Cableado instalado

Numeración BCM, LEDs activos en alto, resistencia individual y tierra común.

| Posición | Herramienta | GPIO BCM |
|---|---|---|
| 1 | Multímetro | 17 |
| 2 | Osciloscopio | 27 |
| 3 | Generador de funciones | 22 |
| 4 | Fuente de banco | 23 |
| 5 | Estación de soldadura | 24 |
| 6 | Cautín | 25 |
| 7 | Servomotor | 16 |
| 8 | Capacitor variable | 26 |
| 9 | Adaptador de corriente | 12 |

## Servicios y pantalla

Se emplean servicios del usuario para la aplicación, la pantalla y la preparación del teléfono. El escritorio inicia sesión automáticamente como `testuser`.

| Servicio | Función |
|---|---|
| `kiwi.service` | FastAPI con HTTPS en el puerto 8000; espera acceso a GPIO al iniciar |
| `kiwi-local-screen.service` | Puente de pantalla ligado solo a 127.0.0.1:8002, verifica TLS del servidor |
| `kiwi-kiosk.service` | Firefox con perfil dedicado, pantalla completa en localhost:8002 |
| `kiwi-phone-setup.service` | Página local en el puerto 8081 para instalar el certificado del iPhone |

La pantalla principal utiliza Firefox; Chromium mostraba una página blanca en este entorno. El puente local permite administración desde la Pi sin exponerla al Wi-Fi. La cámara sigue usando HTTPS verificado.

Ambas pantallas utilizan la identidad visual de referencia de KEY Institute: azul `#006dff`, verde `#31e083`, negro y blanco; logotipo SVG servido localmente y Albert Sans con su licencia OFL. Los recursos están en `kiwi_pi/static`. Se comprobó el diseño sin desplazamiento en 800×480 y 390×844.

La CA instalada en el iPhone se conserva. `kiwi-tools/prepare_network.py` se ejecuta antes del servidor: detecta la IPv4 Wi-Fi, verifica o reemite el certificado con la misma CA, actualiza `server.public_url`, `camera-url.txt` y el QR público. El puente de pantalla conecta por TCP a 127.0.0.1 y usa `kite.local` únicamente para verificar TLS; no necesita resolver ese nombre. El teléfono utiliza la IP y su SAN verificado. Si no hay Wi-Fi, se conserva la dirección anterior para permitir abrir la pantalla y corregir la red. Tras cambiar de red con el equipo ya encendido, reiniciar los servicios.

El menú **☰ → Salir al escritorio** pausa, borra las guías y detiene únicamente `kiwi-kiosk.service`. Requiere conexión local y `server.enable_desktop_exit: true`. Desde el escritorio se usa el menú Raspberry → Shutdown para apagar; no existe una ruta remota de apagado. Los servicios del usuario y Avahi están habilitados, y labwc/XDG inician el kiosco al entrar al escritorio. El acceso `Kiwi herramientas` permite reabrirlo.

Referencia visual: https://www.keyinstitute.com/ . Logo original: https://cdn.prod.website-files.com/6682c401b99fdde3e09052cb/669777573c28975423efdc16_logo.svg . Fuente Albert Sans: Google Fonts, licencia en `static/fonts/OFL.txt`.

```bash
systemctl --user status kiwi.service kiwi-local-screen.service kiwi-kiosk.service
systemctl --user restart kiwi.service kiwi-local-screen.service kiwi-kiosk.service
```

La configuración está en `config.yaml`; las claves están en `.env` con permisos 600. La CA y los certificados están en `/home/testuser/kiwi-https-pruebas`. Las claves de Colab nunca se envían al teléfono.

Las utilidades de esta instalación están en `/home/testuser/kiwi-tools`. El respaldo previo a separar las pantallas está en `/home/testuser/kiwi-tools/kiwi-before-camera-split.tgz`. El inicio anterior del escritorio se conserva en `/home/testuser/.config/labwc/autostart.before-kiwi-20261013`.

## Recuperar Colab

Conectar el entorno GPU y ejecutar configuración/importaciones, funciones de decisión, Qwen (5b), Grounding DINO (5c), dependencias REST y API (6b). La sección 6 de Gradio no es necesaria para la webcam. Los secretos `KIWI_API_KEY` y `NGROK_AUTHTOKEN` deben seguir habilitados.

Si cambia el enlace de ngrok, actualizar el endpoint en la configuración local o en `/legacy?diag` desde la Pi. El ajuste guardado en `data/settings.json` tiene prioridad sobre `config.yaml`. Verificar el indicador **API en línea**.

## Detalles de prueba

El teléfono envía JPEG de hasta 960 px, con una solicitud de envío a la vez. La Pi conserva el cuadro más reciente y procesa como máximo uno por segundo, sin cola de imágenes antiguas. La vista **Ver análisis** muestra la imagen correspondiente a los recuadros; **Ver cámara** regresa al cuadro reciente.

Se añadió supresión de propuestas superpuestas o contenidas antes del seguimiento. En la imagen real del multímetro, DINO proponía nueve recuadros para el instrumento y sus partes, impidiendo estabilidad. El filtro conserva la propuesta más confiable; la prueba con la API real confirmó `digital_multimeter` en el cuarto cuadro. Instrumentos separados deben presentarse sin superposición; el filtro puede ocultar un objeto colocado dentro de otro.

La secuencia física de los nueve LEDs y el reconocimiento anterior del multímetro fueron confirmados por el usuario. La nueva separación cámara/estación tiene pruebas automatizadas de permisos, pérdida de cámara, descarte de respuestas después de finalizar, memoria acotada y repetición del mismo objeto.
