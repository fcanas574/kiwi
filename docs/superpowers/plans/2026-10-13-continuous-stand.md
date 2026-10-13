# Continuous Stand Scanning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que un celular dedicado escanee herramientas automáticamente, consulte Qwen solo cuando sea necesario y conserve las devoluciones hasta su confirmación.

**Architecture:** La página HTTPS de la Raspberry recibe cuadros recientes del celular. Un coordinador en la Pi usa DINO remoto, seguimiento geométrico local y clasificación Qwen remota selectiva; la estación acumula objetivos sin reiniciar sensores ni LEDs. Captura, seguimiento y ciclo de devolución tienen estados distintos.

**Tech Stack:** Python/FastAPI/httpx/Pillow, modelos Transformers existentes en Colab, JavaScript del navegador, pytest y Node; TLS local con OpenSSL y servidor Uvicorn existente.

**Spec:** `docs/superpowers/specs/2026-10-13-continuous-stand-design.md`, aprobada por el usuario el 13 de octubre de 2026.

## Global Constraints

- Raspberry Pi 4 B; DINO y Qwen permanecen en Colab. No instalar modelos ni análisis de nitidez en la Pi.
- Celular dedicado, misma Wi-Fi, HTTPS local, cámara trasera y página en primer plano. Credencial de emparejamiento distinta de la clave Colab.
- Captura inicial máxima de 1 FPS, JPEG con lado mayor de 960 px; máximo 12 detecciones. Una inferencia remota de escaneo en curso; sin cola de imágenes.
- Vista previa solicitada a 15–30 FPS; no prometer frecuencia efectiva hasta medirla.
- Asociación IoU >= 0.3, ambigüedad si diferencia < 0.1. Estabilidad: tres detecciones, al menos un segundo, IoU >= 0.6, variación de área <= 15 %, centro <= 2 % de la diagonal.
- Dos muestreos faltantes tolerados; al tercero retirar el seguimiento. Un intervalo > 10 s invalida continuidad, configurable para CPU lenta.
- Qwen provisional hasta detección DINO posterior compatible; no activar LED a partir de un resultado provisional.
- Rechazo: nueva estabilidad tras movimiento y mínimo 5 s entre consultas. Rearme: tres detecciones válidas de ausencia; errores o pausa no cuentan.
- Categorías repetidas comparten ubicación y sensor. No verificar cantidad ni identidad depositada.
- Guías continuas no vencen por timeout; modo manual conserva el timeout existente. Pausar conserva guías; cancelar pausa e invalida solicitudes.
- Un celular emisor activo; reconexión crea nueva generación, conserva guías y supresiones. Reinicio del servidor empieza pausado con LEDs apagados.
- Corrección: hasta 12 recortes del último cuadro y una copia congelada con TTL de 300 s. Generación de la corrección se asigna atómicamente al pausar.
- Un bloqueo de modelos compartido por DINO, Qwen, `/predict` y Gradio. Sin GPU concurrente en esta versión.
- Conservar criterios de aceptación, categorías, aspecto de la interfaz y drivers. Sin filtros nuevos de fondo, WebRTC, entrenamiento ni refactorizaciones ajenas.
- Prefijar comandos con `rtk`. Crear worktree administrado al ejecutar, no durante esta planificación. No exponer claves o certificados privados en Git.

## Review Focus

- Una herramienta cambia mientras Qwen responde: confirmar con DINO posterior y descartar resultado incompatible; Tasks 2/4.
- Pérdida de Wi-Fi después de una devolución: no contar cuadros inexistentes como ausencia ni rearmar LEDs al reconectar; Tasks 2/4.
- Nuevo ciclo durante el período verde anterior: el temporizador anterior no cancela los objetivos nuevos; Task 3.
- Abrir una corrección incrementa generación: conservar el recorte congelado válido mientras se descartan inferencias anteriores; Tasks 4/5.
- Certificado aceptado visualmente pero cámara no disponible, o dirección IP cambiante: verificar confianza real y nombre/IP del certificado; Task 6.

---

## Estructura y comprobación inicial

Los módulos existentes conservan sus responsabilidades. Se crean únicamente `tracking.py` (geometría), `scan.py` (coordinación), `scan_api.py` (rutas) y `static/scan.js` (captura/presentación); no ampliar indefinidamente `app.py` o `app.js`.

Para pruebas, usar el entorno ya disponible `/tmp/kiwi-merge-venv/bin/python` si existe; en otra máquina crear `pi/.venv` e instalar `pi/requirements-dev.txt`. En los comandos siguientes `python` es el intérprete del entorno preparado y `node` debe ser >= 18. Ejecutar una vez la suite existente como línea base.

Comandos de referencia:

```bash
rtk proxy env PYTHONPATH=pi:colab python -m pytest pi/tests colab/test_multiobject_probe.py -q
rtk proxy node --test pi/tests/test_ui_multiobject.cjs pi/tests/test_ui_requests.cjs
```

### Task 1: DINO y Qwen como operaciones remotas separadas

**Files:** modificar `colab/kiwi_multiobject.py`, `Kiwi_EmbeddingGemma2_Colab.ipynb`, `pi/kiwi_pi/recognizer.py`; crear `colab/test_scan_recognizer.py` y `pi/tests/test_scan_contract.py`; ampliar `pi/tests/test_colab_contract.py`.

**Interfaces:**
- `MultiObjectRecognizer.locate(image: PIL.Image, threshold: float = .25) -> dict`: `{image_size, objects:[{id, box, detector_label, detector_score}], raw_detections, detection_seconds}`; cuadros enteros, normalización EXIF y RGB. Reutiliza `detect()` y su NMS.
- `MultiObjectRecognizer.classify_crop(image: PIL.Image) -> dict`: resultado Qwen existente. `identify()` compone localización y clasificación, manteniendo respuesta de tres salidas y alias histórico.
- `Recognizer.detect(jpeg: bytes, correlation: dict) -> dict` y `Recognizer.classify(jpeg: bytes, correlation: dict) -> dict`, asíncronos. REST deriva `/detect` y `/classify` de la misma base configurada para `/predict`; Mock implementa ambas sin modelos. Gradio mantiene modo manual y produce error explícito si se intenta escaneo con ese driver.
- `validate_detections(payload: dict, image_size: tuple[int,int]) -> list[dict]` y `validate_classification(payload: dict) -> dict`: IDs, cuadros, puntuaciones y aceptación coherentes antes de usar resultados.
- Colab `/detect` y `/classify` aceptan archivo y JSON `correlation` en multipart y devuelven `{correlation, result}`. Ambos comparten auth Bearer, límite actual de 15 MB y bloqueo existente; `/health` anuncia capacidades disponibles.

- [ ] Escribir `test_locate_does_not_call_qwen`, `test_classify_does_not_call_dino` y `test_manual_identify_contract_unchanged`: contar llamadas de dobles, comprobar lista vacía y máximo 12.

```python
result = model.locate(image)  # model usa detector y clasificador dobles
assert calls['qwen'] == 0
assert result['image_size'] == list(image.size)
assert len(result['objects']) <= 12
```
- [ ] Escribir `test_split_endpoints_share_lock_and_auth`, `test_correlation_is_echoed`, `test_invalid_detect_contract_rejected` y `test_gradio_scan_not_supported`; extraer helpers del notebook para comprobar contrato sin descargar pesos.
- [ ] Ejecutar `rtk proxy env PYTHONPATH=pi:colab python -m pytest colab/test_scan_recognizer.py pi/tests/test_scan_contract.py pi/tests/test_colab_contract.py -q`; comprobar fallos por interfaces ausentes.
- [ ] Implementar las interfaces anteriores y las celdas inline del notebook; preservar `/predict` y Gradio mediante composición de los mismos helpers. No introducir una segunda clasificación ni doble adquisición del bloqueo.
- [ ] Repetir el comando anterior junto con `colab/test_multiobject_probe.py`; comprobar PASS y analizar con `ast.parse` todas las celdas Python omitiendo líneas mágicas de instalación.
- [ ] Commit: `rtk git add colab/kiwi_multiobject.py colab/test_scan_recognizer.py Kiwi_EmbeddingGemma2_Colab.ipynb pi/kiwi_pi/recognizer.py pi/tests/test_scan_contract.py pi/tests/test_colab_contract.py`, luego `rtk git commit -m 'Separate detection and crop classification for continuous scanning'`.

### Task 2: Seguimiento, estabilidad y clasificación provisional

**Files:** crear `pi/kiwi_pi/tracking.py`, `pi/tests/test_tracking.py`; modificar `pi/config.example.yaml`.

**Interfaces:**
- `Tracker(rules: dict)` mantiene como máximo 12 seguimientos, usa tiempo suministrado y no depende de red ni GPIO.
- `update(frame_id: int, image_size: tuple[int,int], detections: list[dict], now: float) -> list[dict]`: actualiza geometría y devuelve aceptaciones/rechazos Qwen provisionales que este cuadro acaba de confirmar. Cada evento incluye `track_id`, `revision`, `box`, `recognition` y `frame_id`.
- `next_candidate(now: float) -> dict | None`: reserva el elegible que más tiempo lleva esperando; devuelve `{track_id, revision, frame_id, box}` y evita reservarlo dos veces.
- `set_provisional(track_id: int, revision: int, recognition: dict, now: float) -> None`, `classification_failed(track_id: int, revision: int, now: float) -> None`, `reset() -> None`, `snapshot() -> list[dict]`.
- `category_visibility() -> dict[str, bool | None]`: presencia de categorías conocidas en el último muestreo válido; `None` para asociación incierta. Incertidumbre no sirve como evidencia de ausencia.
- Configuración `scan`: `max_fps: 1`, `max_side: 960`, `association_iou: .3`, `ambiguity_delta: .1`, `stable_samples: 3`, `stable_seconds: 1`, `stable_iou: .6`, `area_tolerance: .15`, `center_tolerance: .02`, `missing_samples: 3`, `max_gap_seconds: 10`, `retry_seconds: 5`, `rearm_samples: 3`.

- [ ] Escribir prueba con observaciones iguales en `now=0,.5,1`: antes de la tercera `next_candidate()` es None y en la tercera hay un candidato. Añadir casos de movimiento, escala, ambigüedad y coordenadas normalizadas al cambiar resolución.

```python
tracker.update(1, (960, 720), detections, 0)
assert tracker.next_candidate(0) is None
tracker.update(2, (960, 720), detections, .5)
assert tracker.next_candidate(.5) is None
tracker.update(3, (960, 720), detections, 1)
assert tracker.next_candidate(1) is not None
assert tracker.next_candidate(1) is None  # reserva única
```
- [ ] Escribir `test_qwen_needs_later_detection`, `test_changed_tool_discards_provisional`, `test_two_missing_samples_preserve_track`, `test_third_missing_sample_expires_track` y `test_gap_over_ten_seconds_resets_stability`.
- [ ] Escribir `test_accepted_track_not_reclassified`, `test_rejection_requires_change_and_cooldown` y `test_candidates_are_fair`; comprobar que cuadros inmóviles rechazados no generan nuevas consultas y que asociaciones inciertas no declaran ausencia de su categoría anterior.
- [ ] Ejecutar `rtk proxy env PYTHONPATH=pi python -m pytest pi/tests/test_tracking.py -q`; comprobar RED.
- [ ] Implementar `Tracker` con asociación uno a uno por IoU, referencia de estabilidad y contador de muestras. Usar coordenadas normalizadas y reloj monotónico externo; no añadir librerías de tracking ni embeddings. Añadir parámetros al YAML.
- [ ] Repetir las pruebas y comprobar PASS; commit de `tracking.py`, sus pruebas y configuración con mensaje `Add lightweight tracking and stable selective classification`.

### Task 3: Guías acumuladas que sobreviven al escaneo

**Files:** modificar `pi/kiwi_pi/station.py`, `pi/tests/test_multiobject_station.py`; crear `pi/tests/test_continuous_station.py`.

**Interfaces:** `Station.add_guides(slots: list[Slot], operation_id: str, test_mode: bool = False) -> None` inicia un ciclo continuo o añade objetivos al ciclo activo. `snapshot()['guide']` añade `continuous: bool`; conservar `begin_guides()` para fotos manuales. Añadir un contador interno de ciclo para proteger temporizadores anteriores.

- [ ] Escribir `test_add_target_keeps_confirmed_and_wrong_slots`, `test_new_target_checks_current_sensor_occupancy` y `test_duplicate_target_does_not_reset_progress`.

```python
st.add_guides([cfg.slot_by_id(1)], 'cycle-a')
await sensor(st, 1, True)
st.add_guides([cfg.slot_by_id(7)], 'cycle-a')
assert st.confirmed == {1}
assert st.pending == {7}
assert st.operation_id == 'cycle-a'
```

El caso anterior añade el segundo objetivo durante el período verde y antes de cerrar el ciclo; el caso de temporizador añade un objetivo con otro `operation_id` y exige que el temporizador previo no lo cancele.
- [ ] Escribir `test_continuous_guide_does_not_expire`, `test_old_success_timer_cannot_cancel_new_cycle`, `test_pause_does_not_disable_sensors` y `test_no_sensors_never_completes`; conservar los casos actuales de muestreo simultáneo y timeout manual.
- [ ] Ejecutar `rtk proxy env PYTHONPATH=pi python -m pytest pi/tests/test_continuous_station.py pi/tests/test_multiobject_station.py -q`; confirmar RED.
- [ ] Implementar `add_guides`, deduplicación, bloqueo inicial por sensor y temporizadores ligados al ciclo. No llamar `cancel_guide()` al añadir objetivos; sin timeout destructivo en modo continuo. Evaluar finalización únicamente después del lote completo de sensores, manteniendo el comportamiento manual.
- [ ] Repetir las pruebas anteriores con `pi/tests/test_station.py`; confirmar PASS.
- [ ] Commit de estación y pruebas con mensaje `Accumulate continuous return targets without resetting sensors`.

### Task 4: Coordinación asíncrona y API de escaneo

**Files:** crear `pi/kiwi_pi/scan.py`, `pi/kiwi_pi/scan_api.py`, `pi/tests/test_scan.py`, `pi/tests/test_scan_api.py`; modificar `pi/kiwi_pi/app.py`, `pi/kiwi_pi/config.py` y `pi/config.example.yaml`.

**Interfaces:**
- `ScanCoordinator(station: Station, recognizer: Recognizer, cfg: Config, clock: Callable[[],float])` consume Tasks 1–3. Expone `start(owner: str, session_id: str | None = None) -> dict`, `pause(session_id: str) -> dict`, `cancel(session_id: str) -> dict`, `snapshot() -> dict`, `close() -> Awaitable[None]`.
- `process_frame(session_id: str, generation: int, sequence: int, jpeg: bytes) -> Awaitable[dict]`: valida propietario/generación/secuencia, normaliza fuera del loop, ejecuta DINO, aplica provisionales confirmados y consulta como máximo un Qwen elegible. Devuelve el estado y detecciones vinculadas al cuadro; clasificación nueva queda provisional para el siguiente cuadro.
- `freeze_correction(session_id: str, track_id: int, revision: int) -> dict` pausa e incrementa generación atómicamente, devuelve `{correction_id, generation, expires_at}`. `submit_correction(correction_id: str, actual: str) -> dict` guarda recorte/metadatos y cancela ciclo; `cancel_correction(correction_id: str) -> None` libera la copia.
- `register_scan_routes(app: FastAPI, get_scan: Callable[[],ScanCoordinator], cfg: Config) -> None` en `scan_api.py`. Rutas start/pause/cancel, multipart frame, correction/prepare, correction/submit, correction/cancel y lectura del recorte congelado autenticada.
- Escrituras requieren emparejamiento mediante cookie HttpOnly/SameSite emitida por `POST /api/scan/pair`; secreto `KIWI_SCAN_PAIRING_KEY` del entorno. No colocarlo en QR ni URL. El navegador introduce el código una vez; no recibe `KIWI_API_KEY`.
- Correlación remota: `{session_id, generation, sequence, track_id?, revision?}`. HTTP 401 sin emparejar, 409 para sesión/generación/secuencia incorrecta o servidor ocupado, 404 para recorte inexistente y 410 para caducado.
- Estado: `{session_id, generation, capture_state, last_sequence, busy, tracks, connection_error, retry_after_seconds, detection_seconds, classification_seconds}`; guía conserva operación independiente.

- [ ] Escribir `test_one_remote_inference_at_a_time`, `test_pause_discards_late_response_but_keeps_guide`, `test_foreign_session_cannot_take_over` y `test_sensor_changes_processed_while_colab_waits` usando dobles bloqueados por un evento, sin esperas reales largas.
- [ ] Escribir `test_completed_visible_category_is_suppressed`, `test_three_valid_absences_rearm`, `test_network_failure_does_not_rearm` y `test_reconnect_keeps_pending_and_suppression`; observar que categorías confirmadas no reingresan al mismo ciclo.

```python
# Dobles: Colab mantiene detect() esperando un asyncio.Event.
session = scan.start('phone')
task = asyncio.create_task(scan.process_frame(
    session['session_id'], session['generation'], 1, jpeg))
await detection_started.wait()
scan.pause(session['session_id'])
release_detection.set()
await task
assert scan.snapshot()['capture_state'] == 'paused'
assert station.pending == pending_before_request
assert station.operation_id == operation_before_request
```
- [ ] Escribir `test_backoff_is_bounded`, `test_storage_stays_bounded_during_outage` y `test_manual_recognition_cannot_replace_continuous_guide`; comprobar esperas 1,2,4,8,15 y recuperación tras éxito sin cola ni imágenes históricas.
- [ ] Escribir `test_frozen_crop_survives_pause_generation`, `test_expired_or_foreign_correction_saves_nothing`, `test_correction_saves_only_crop` y `test_unpaired_client_cannot_send_frames`. Comprobar máximo 12 recortes corrientes, copia adicional única y TTL 300 s.
- [ ] Ejecutar `rtk proxy env PYTHONPATH=pi python -m pytest pi/tests/test_scan.py pi/tests/test_scan_api.py -q`; confirmar RED.
- [ ] Implementar coordinador con exclusión inmediata de cuadros extra, controles de generación después de cada await y supresiones por categoría. Un error no modifica guías ni contadores de ausencia. Aplicar nuevas ubicaciones mediante `add_guides()`; terminar el ciclo no termina la sesión.
- [ ] Implementar rutas en su módulo y conectarlas al lifespan y eventos existentes de `app.py`. Normalizar/cortar JPEG con `asyncio.to_thread`; conservar validación antes de LEDs. Añadir configuración de emparejamiento y backoff; impedir el reemplazo manual de guía continua con 409.
- [ ] Ejecutar las pruebas anteriores y `pi/tests/test_multiobject_api.py`, `pi/tests/test_continuous_station.py`, `pi/tests/test_tracking.py`; confirmar PASS.
- [ ] Commit de archivos de esta tarea con mensaje `Coordinate continuous scanning with persistent returns and sessions`.

### Task 5: Página de inicio, pausa y corrección desde el celular

**Files:** crear `pi/kiwi_pi/static/scan.js`, `pi/tests/test_ui_scan.cjs`; modificar `pi/kiwi_pi/static/index.html`, `app.js`, `styles.css`, `UX-CONTRACT.md` y `premium-ui.json`.

**Interfaces:** `KiwiScan.createController({transport, camera, clock, onState})` expone `start()`, `pause()`, `cancel()`, `dispose()` y `getState()`. Las dependencias se inyectan para pruebas Node; el adaptador navegador usa rutas de Task 4. `transport.sendFrame({session_id,generation,sequence,jpeg})` devuelve estado/correlación; `camera.capture({maxSide:960})` devuelve JPEG y vista congelada del mismo cuadro.

- [ ] Escribir pruebas Node: cero envíos antes de iniciar; uno en curso; límite 1 FPS; nueva captura tras finalizar; fallo no provoca bucle rápido; pause/cancel descartan respuestas tardías; reinicio usa nueva generación; imagen anotada corresponde al cuadro procesado.

```javascript
assert.equal(controller.getState().capture_state, 'paused');
assert.equal(sentFrames.length, 0);
await controller.start();
await advanceClock(1000); // reloj y transporte controlados por la prueba
assert.equal(sentFrames.length, 1);
await advanceClock(1000); // sendFrame anterior sigue sin resolver
assert.equal(sentFrames.length, 1);
await controller.pause();
resolveFrame(oldResponse);
await flushPromises();
assert.equal(controller.getState().capture_state, 'paused');
```
- [ ] Ejecutar `rtk proxy node --test pi/tests/test_ui_scan.cjs`; confirmar RED.
- [ ] Implementar controlador y su adaptador en `scan.js`, manteniendo el código geométrico en la Pi. Cámara trasera, lado mayor 960, vista previa 15–30 FPS y control por visibilidad; gestionar permiso denegado y navegador sin contexto seguro con mensajes concretos.
- [ ] Integrar controles visibles «Iniciar escaneo», «Pausar escaneo», «Cancelar devoluciones» y emparejamiento en la página que sirve la Raspberry. Mostrar video, último cuadro analizado, estabilidad/clasificación, estado de conexión y mapa persistente. No reiniciar captura al confirmar una ubicación.
- [ ] Integrar corrección congelada autenticada: abrir pausa, cancelar modal conserva guías, enviar cancela ciclo y mantiene pausa. Conservar Escape, foco, teclado, diseño móvil y controles manuales sin permitir reemplazo accidental de un ciclo continuo.
- [ ] Ejecutar todas las pruebas Node y comprobar PASS. En navegador con simulador, verificar iniciar/pausar, añadir tres objetivos en cuadros distintos, confirmar uno y añadir otro, error de ubicación, finalizar sin detener escaneo, corregir recorte y layout de 390×844. Registrar recorrido; actualizar contrato UX y manifiesto de verificación.
- [ ] Commit de UI, pruebas y documentación UX con mensaje `Add continuous scan controls to the Raspberry web page`.

### Task 6: HTTPS local y preparación del celular dedicado

**Files:** crear `pi/deploy/setup-local-https.sh`, `pi/tests/test_https_config.py`, `docs/local-phone-setup.md`; modificar `pi/kiwi_pi/__main__.py`, `pi/config.example.yaml`, `pi/deploy/install.sh`, `pi/deploy/kiosk.sh`, `pi/README.md` y `.gitignore`.

**Interfaces:** configuración `server.tls_certfile` y `server.tls_keyfile`; ambos vacíos conservan HTTP de desarrollo, ambos definidos habilitan TLS en Uvicorn, solo uno provoca error claro. `server.public_url` contiene `https://<IP-reservada>:8000` para QR/kiosco cuando hay TLS, evitando abrir `localhost` con un certificado emitido para la IP. `rtk proxy bash pi/deploy/setup-local-https.sh --ip <IP-reservada> --output <directorio-privado>` crea CA local y certificado servidor con SAN IP, usando OpenSSL instalado en la Pi; no descarga certificados ni modifica confianza del teléfono automáticamente.

- [ ] Escribir pruebas que capturen argumentos Uvicorn para HTTP y HTTPS y rechacen configuración incompleta. Probar en carpeta temporal que el certificado verifica con la CA, incluye IP SAN, tiene EKU serverAuth y las claves no tienen permisos de lectura para otros usuarios; no copiar claves a fixtures.

```python
assert uvicorn_options['ssl_certfile'] == str(server_cert)
assert uvicorn_options['ssl_keyfile'] == str(server_key)
assert server_key.stat().st_mode & 0o077 == 0
assert ca_key.stat().st_mode & 0o077 == 0
assert configured_public_url == 'https://192.168.1.20:8000'
```
- [ ] Ejecutar `rtk proxy env PYTHONPATH=pi python -m pytest pi/tests/test_https_config.py -q`; confirmar RED.
- [ ] Implementar script con validación de IP, CA y certificado por 365 días, claves chmod 600, rechazo de sobrescritura accidental y salida con rutas de configuración. Mantener material privado fuera de Git; crear QR mediante `qrencode`, paquete de sistema instalado explícitamente por el despliegue, con URL local sin credenciales y salida PNG en el directorio elegido. No enviar URLs a un servicio externo.
- [ ] Implementar parámetros TLS y actualizar instalación/kiosco para abrir la URL configurada. Documentar confianza de CA para Android/iOS y para el kiosco de la propia Pi, reserva DHCP, reinicio del servicio, renovación y comprobación de cámara real; no asumir que saltar el aviso del navegador equivale a confiar la CA.
- [ ] Repetir pruebas y ejecutar `rtk proxy bash -n pi/deploy/setup-local-https.sh pi/deploy/install.sh pi/deploy/kiosk.sh`; comprobar PASS. La confianza del teléfono y GPIO requieren prueba física posterior y no se declaran verificadas desde escritorio.
- [ ] Commit de HTTPS y documentación con mensaje `Support local HTTPS for the dedicated phone camera`.

### Task 7: Recorrido completo, mediciones y revisión

**Files:** crear `pi/tests/test_scan_end_to_end.py`, `docs/continuous-stand-validation.md`; modificar `README.md`, `pi/README.md`, el plan y la especificación solo para registrar estado final y límites comprobados.

**Interfaces:** consume los contratos ya definidos; no introduce componentes ni optimizaciones adicionales.

- [x] Escribir una prueba integral con endpoints simulados y reloj controlado: estabilidad → Qwen provisional → siguiente detección confirma → primer LED → nuevo objetivo sin borrar confirmación → sensor final → escaneo sigue activo → ausencia válida permite otro ciclo. Incluir fallo de Colab, reconexión y respuesta atrasada.

```python
assert state_after_qwen['guide']['targets'] == []
assert state_after_confirmation_frame['guide']['pending'] == [1]
assert state_after_new_target['guide']['confirmed'] == [1]
assert state_after_new_target['guide']['pending'] == [7]
assert state_after_final_sensor['guide']['phase'] == 'returned'
assert state_after_final_sensor['scan']['capture_state'] == 'running'
```
- [x] Ejecutar `rtk proxy env PYTHONPATH=pi:colab python -m pytest pi/tests/test_scan_end_to_end.py -q`; si falla, corregir únicamente la integración responsable y repetir sus pruebas.
- [x] Ejecutar suite completa Python y todos los `pi/tests/test_ui_*.cjs`; validar sintaxis de notebook y `rtk git diff --check`. Registrar comandos y resultados en el documento de validación.
- [x] Repetir el recorrido web con simulador y verificar que solicitudes lentas no bloquean eventos de sensores. Documentar comprobaciones de GPU, certificado/teléfono y Pi física como pendientes si el equipo no está disponible.
- [ ] En equipo disponible, medir CPU/RAM, cadencia de sensores y retraso añadido frente al modo sin escaneo. Meta: <= 100 ms adicionales para un evento ya antirreboteado y cero cambios perdidos; medir DINO/Qwen y tiempo hasta LED con uno y varios objetos. No sustituir medidas físicas por estimaciones. **Pendiente: no hay Raspberry Pi, teléfono ni GPU disponibles.**
- [x] Actualizar README con inicio desde página Raspberry/celular, emparejamiento, HTTPS local, controles, pausa/reconexión, supresiones, CPU lenta y uso manual conservado. Explicar que cambiar de celular requiere preparar su confianza de certificado.
- [x] Solicitar revisión independiente del cambio completo con especial atención a las cinco condiciones de Review Focus. Resolver hallazgos importantes con reproducciones fallidas antes de corregir y repetir las pruebas afectadas; seguir el protocolo del método de ejecución elegido.
- [x] Guardar evidencia, commits y limitaciones; marcar pasos efectivamente completados. Entregar la rama revisable y los enlaces de notebook/documentación. Publicación e integración siguen el alcance que el usuario autorice para esta nueva implementación; no activar servicios o cámaras en equipos ajenos automáticamente.

## Revisión del plan y ejecución

Cobertura comprobada contra la especificación: separación de modelos (1), estabilidad y provisionalidad (2), sensores/ciclos (3), sesiones/rearme/correcciones (4), página web (5), teléfono/HTTPS (6), pruebas y documentación (7). Las cinco condiciones de revisión tienen pruebas asignadas. No se omiten las verificaciones físicas cuando no haya hardware: se registran pendientes explícitamente.

Se recomienda ejecución nativa en esta sesión y una revisión independiente al final: las tareas comparten contratos y ciclos de estado, por lo que un implementador continuo reduce inconsistencias. La alternativa es ejecución con un subagente y un revisor por tarea. El usuario revisará este plan y confirmará el método antes de modificar código de producto.
