# Validación del escaneo continuo

Estado al 13 de octubre de 2026: las rutas de software y el navegador simulado completan los recorridos indicados abajo. La revisión final de la rama encontró dos fallos de integración; la corrección final y su revisión independiente enfocada resolvieron todos los hallazgos accionables. No se hicieron mediciones con Raspberry Pi, teléfono ni GPU; no se declara cumplida la meta física de 100 ms.

## Recorrido de la aplicación

`pi/tests/test_scan_end_to_end.py` usa `create_app`, sus rutas reales de emparejamiento, captura y sensores, `ScanCoordinator`, `Station`, el controlador LED y el monitor de sensores con su tick activo. Un reconocedor controlado entrega respuestas de DINO/Qwen, puede fallar o mantener una respuesta pendiente, y recibe un reloj monotónico controlable. No carga modelos ni simula directamente cambios internos de guía o LED.

El recorrido confirmó que una respuesta Qwen provisional no crea destinos ni enciende LEDs; la detección DINO siguiente crea el primer destino. Una transición por `/api/sim/slots/1` confirma el sensor mientras una solicitud DINO continúa ocupada. Luego se añade el destino 7 y se conserva la confirmación del 1; el sensor final deja el estado en `returned` con captura `running`. Tres detecciones válidas de ausencia permiten rearmar una categoría y abrir un ciclo nuevo. También se comprobaron el error de Colab, la reconexión con nueva generación y el descarte de una respuesta anterior que llega después de otro evento de sensor.

```text
rtk proxy env PYTHONPATH=pi:colab /tmp/kiwi-merge-venv/bin/python -m pytest pi/tests/test_scan_end_to_end.py -q
1 passed, 1 warning in 0.49s; exit 0
```

La advertencia es la deprecación existente de Starlette sobre `httpx` en `TestClient`.

## Verificación de navegador

Task 5 ejecutó el flujo completo en Chrome con viewport 390×844, cámara Y4M sintética y servidor simulado: emparejamiento, pausa/reinicio, varios objetos, sensores, guía parcial y final, corrección congelada y cancelación. El reporte aceptado conserva los detalles del recorrido; esta captura queda junto a la documentación: [flujo completo de Task 5](continuous-stand-full-flow.png).

La comprobación enfocada de Task 7 ejecutó el Chrome real de esta Mac contra la aplicación y sus rutas locales, con cámara falsa y mock de Colab. El quinto llamado DINO se retrasó cuatro segundos. Durante esa espera, el sensor 1 pasó a ocupado, su guía quedó confirmada y el LED lógico cambió a `ok`, mientras `scan.busy` seguía en `true`. Al terminar DINO, la estación mantuvo `[1]` confirmado y `[6, 7]` pendientes; la captura continuó activa. No hubo errores de página ni recursos. Captura: [sensor confirmado durante la solicitud lenta](continuous-stand-slow-request.png).

```text
rtk proxy /Users/felipecanas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node /tmp/kiwi-task7-slow-sensor-browser.cjs
{"delayedDetectionCall":5,"heldRequestMs":4000,"sensorProcessedWhileBusy":true,"sensorState":true,"ledState":"ok","confirmed":[1],"pending":[6,7],"captureState":"running","pageErrors":[],"resourceFailures":[]}
exit 0
```

Este recorrido usó `--use-fake-device-for-media-stream` y `/tmp/kiwi-stand-fake-camera.y4m`; no abrió una cámara física. El script del navegador y el servidor temporal vivieron en `/tmp` y no forman parte de la aplicación.

## Suite de software antes de la corrección final

La suite Python completa incluyó los casos optativos de Chromium/red de `test_kiosk_network.py` con `KIWI_TEST_CHROME` configurado:

```text
rtk proxy env PYTHONPATH=pi:colab KIWI_TEST_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' /tmp/kiwi-merge-venv/bin/python -m pytest pi/tests colab -q
147 passed, 1 warning in 20.31s; exit 0
```

El warning es el mismo aviso de `TestClient`. Todas las pruebas de interfaz CJS pasaron:

```text
rtk proxy /Users/felipecanas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test pi/tests/test_ui_*.cjs
26 tests, 26 passed, 0 failed; exit 0
```

La comprobación AST recorrió **todas** las celdas de código de los dos notebooks. Para cada celda, eliminó únicamente las líneas cuyo `lstrip()` empieza con `%` o `!`, y pasó el resto a `ast.parse`; no omitió celdas completas. `Kiwi_EmbeddingGemma2_Colab.ipynb`: 11/11 celdas analizadas; `Kiwi_Multiobject_Probe_Colab.ipynb`: 6/6; salida 0. `rtk proxy bash -n pi/deploy/setup-local-https.sh pi/deploy/install.sh pi/deploy/kiosk.sh` también salió con código 0. El `rtk git diff --check` final se registra en el reporte de Task 7 después de cerrar este documento.

## Corrección final y verificación actual

La pérdida de cámara se distingue del error de transporte. Un fallo de `capture()` o un evento `ended` de la pista de video detiene la captura local, invalida sus respuestas pendientes y solicita la pausa de la Pi con nueva generación. Conserva las guías y sensores; necesita **Iniciar escaneo** explícito para abrir de nuevo la cámara. Los errores de red conservan el reintento creciente existente. Si no se logra entregar la pausa por un fallo de red, la captura local sigue detenida y se muestra el error de conexión; la generación remota solo puede confirmarse cuando la Pi responde.

Una corrección congelada no caducada mantiene validez cuando su propia guía termina naturalmente por sensores y cierra el período verde. Una revisión monotónica de operaciones distingue ese cierre de una sustitución o cancelación explícita. Los sucesores se rechazan aunque ya hayan cerrado o reutilicen el identificador anterior. Se conservan las validaciones de dueño, sesión, generación y TTL, y la escritura del recorte con sus metadatos. El caso integrado atraviesa las rutas reales de preparación, sensores, recorte y envío, y comprueba que el JPEG guardado coincide exactamente con el recorte congelado.

Las dos primeras detecciones vacías ahora comprueban que las categorías siguen suprimidas; la tercera permite el rearme. La prueba integrada avanza el temporizador verde de forma controlada después de comprobar `returned`, eliminando la ventana anterior de 50 ms. La suite de estación conserva las pruebas del temporizador real y su protección de ciclo. Colab también comprueba explícitamente la salida vacía de `locate`, sin llamar a Qwen.

Verificación enfocada: 62 pruebas Python aprobadas, con el aviso existente de TestClient; 23 pruebas del controlador de escaneo aprobadas. Verificación completa después de los cambios:

```text
rtk proxy env PYTHONPATH=pi:colab KIWI_TEST_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' /tmp/kiwi-merge-venv/bin/python -m pytest pi/tests colab -q
154 passed, 1 warning in 19.42s; exit 0

rtk proxy /Users/felipecanas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test pi/tests/test_ui_*.cjs
29 tests, 29 passed, 0 failed; exit 0
```

El aviso de Starlette/httpx no se suprimió ni se migraron dependencias. No se cambiaron notebooks ni scripts de despliegue; sus comprobaciones anteriores no se presentan como nuevas ejecuciones. `rtk git diff --check` pasó en esta corrección.

Una comprobación adicional usó Chrome aislado, video Y4M falso y un servicio local propio en `127.0.0.1:18778`, con la aplicación real y un reconocedor controlado. Detuvo la pista de video y despachó un evento `ended` sintético durante la quinta detección remota, retenida durante tres segundos. La generación pasó de 1 a 2 al pausar; no hubo cuadros posteriores durante cuatro segundos de observación; el sensor 1 confirmó mientras estaba pausado. Pulsar **Iniciar escaneo** abrió la generación 3, retomó cuadros y conservó la guía. No hubo errores de página.

```text
rtk proxy /Users/felipecanas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node /tmp/kiwi-final-camera-browser.cjs
{"generationBefore":1,"generationPaused":2,"generationRestarted":3,"framesAtLoss":5,"subsequentFramesBeforeStart":0,"sensorConfirmedWhilePaused":[1],"guidePreserved":true,"pageErrors":[]}
exit 0
```

Los scripts de esta comprobación vivieron en `/tmp`; el servicio y Chrome se cerraron después. Los primeros intentos del script temporal adelantaban comprobaciones con predicados asíncronos de `waitForFunction`; se corrigió el montaje usando consultas de estado explícitas antes de obtener el resultado registrado. No hubo cambios adicionales de producto por esos intentos. El evento sintético verifica la integración del navegador; no representa una desconexión física ni valida cámara trasera, confianza de certificados o rendimiento real.

## Evidencia y límites anteriores

- La revisión de Task 5 aprobó las correcciones de UI. La auditoría estática estricta terminó con 15 errores `affordance.actionless-button` y 0 avisos: son controles enlazados desde JavaScript que la regla estática no sigue. El resultado es una limitación de la herramienta, no un pase de auditoría. Los controles del escaneo y varias rutas de corrección sí se ejercitaron en Chrome; algunos controles manuales y de diagnóstico solo se comprobaron en el código fuente.
- Task 6 verificó TLS local con OpenSSL, el script de kiosco, direcciones de transporte y límites de administración usando un servidor temporal y Chrome aislado. El navegador local de la Pi y un teléfono no se probaron con su almacén real de confianza.
- `qrencode` no estaba instalado durante Task 6. Un comando QR local de prueba comprobó que el generador entrega solo la URL HTTPS local; no se creó ni decodificó una imagen QR real.
- El uso de Colab real, la GPU, una Raspberry Pi física, GPIO, un teléfono dedicado, cámara trasera y confianza de certificado en esos equipos siguen pendientes. Tampoco se midieron CPU/RAM, cadencia de sensores, frecuencia efectiva de vista previa, tiempo DINO/Qwen ni demora hasta el primer LED.
- La conexión de red del teléfono y la ejecución de la página en primer plano, junto con la prueba de cámara real, deben completarse en el stand preparado. Cambiar de teléfono requiere confiar la CA local en ese nuevo dispositivo.

## Procedimiento para mediciones físicas

No hay cifras estimadas para sustituir las mediciones. Cuando estén disponibles la Pi, el teléfono dedicado y Colab con GPU:

1. Reserva la IP de la Pi, prepara HTTPS y confía la CA en el teléfono y en Chromium del kiosco. Comprueba la cámara trasera y que Kiwi permanezca en primer plano.
2. En la misma Pi, registra cinco minutos sin escaneo y cinco minutos con escaneo, primero con un objeto y luego con varios. Usa `systemctl show -p MainPID --value kiwi` para obtener el proceso y `pidstat -h -u -r -p <PID> 1 300` para CPU y memoria residente; conserva las salidas y el valor exacto de configuración.
3. En un banco instrumentado, registra cada transición de sensor después del antirrebote y la escritura del LED correspondiente con marcas `monotonic_ns`. Repite las mismas transiciones con escaneo apagado y encendido mientras ocurren solicitudes lentas de Colab. Compara la latencia añadida de esos eventos y contrasta el número y orden de transiciones de entrada y salida para detectar pérdidas.
4. Para 10 recorridos con un objeto y 10 con varios, registra `detection_seconds`, `classification_seconds` y el tiempo monotónico desde **Iniciar escaneo** hasta el primer LED. Informa mediana, percentil 95, máximo y fallos; compara la latencia de devolución respecto al modo sin escaneo. La meta de aceptación es como máximo 100 ms adicionales por evento ya antirreboteado y cero cambios perdidos.

No declarar cumplimiento de estas metas hasta guardar resultados tomados en los dispositivos físicos y repetir las verificaciones de teléfono/certificado. La guía de preparación y renovación está en [HTTPS local para la cámara del teléfono](local-phone-setup.md).

## Cierre de revisión

La revisión independiente de toda la rama y la revisión enfocada del parche final están completadas. No quedaron hallazgos críticos o importantes abiertos. Se conservan [los informes y reproducciones](continuous-stand-review.md), [las decisiones de ejecución](continuous-stand-decisions.md) y [el resultado estático de la auditoría de UI](continuous-stand-ui-audit.json). Las comprobaciones físicas y los límites registrados arriba permanecen pendientes.

Comprobación final del controlador sobre el código revisado ca5f675: `PYTHONPATH=pi:colab KIWI_TEST_CHROME=... pytest pi/tests colab -q` terminó con 154 aprobadas, 1 advertencia de dependencia ya existente y código de salida 0 (49,30 s). Todos los `test_ui_*.cjs` terminaron con 29 aprobadas y cero fallos. Las 11 y 6 celdas de los dos notebooks pasaron el análisis de sintaxis; los tres scripts de despliegue pasaron `bash -n` y `git diff --check` no encontró errores. Esta comprobación incluye las pruebas Chrome/red optativas y conserva los límites físicos descritos arriba.
