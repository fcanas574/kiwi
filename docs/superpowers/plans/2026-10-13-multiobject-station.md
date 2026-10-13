# Primary Multiobject Station Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Hacer que Kiwi reconozca varios objetos por foto y guíe todas sus devoluciones simultáneamente.

**Architecture:** Grounding DINO propone cuadros y Qwen clasifica cada recorte. REST y Gradio entregan la misma lista; la Pi la convierte en una operación con ubicaciones pendientes, confirmadas y equivocadas, compartida con la pantalla mediante eventos. Las correcciones guardan recortes individuales.

**Tech Stack:** Python, PyTorch, Transformers, bitsandbytes para CPU, FastAPI, Gradio, JavaScript, drivers de LEDs y sensores existentes, pytest y pruebas Node.

**Spec:** `docs/multiobject-integration-design.md` — aprobado por el usuario el 13 de octubre de 2026.

## Global Constraints

- Flujo principal: fotografía → Grounding DINO → recortes → Qwen3-VL-4B → lista de objetos → LEDs de todas las ubicaciones aceptadas → confirmación independiente por sensores.
- Máximo de 12 recortes; conservar la eliminación de cuadros muy similares del experimento.
- Conservar categorías y hardware actuales; no añadir todavía filtros de fondo.
- Separar las puntuaciones de DINO y Qwen; no presentarlas como probabilidades calibradas.
- Una única ubicación y sensor por categoría: confirmar presencia por ubicación, nunca cantidad ni identidad del objeto depositado.
- Una respuesta atrasada no podrá reactivar LEDs después de cancelar o iniciar otra foto.
- Con sensores desactivados no declarar una devolución verificada.
- Usar el prefijo `rtk` en los comandos, según las instrucciones del proyecto.

## Review Focus

- Respuesta remota malformada: cuadros fuera de imagen, IDs repetidos o campos incompatibles no deben activar luces ni producir correcciones inválidas; Task 3.
- Dos fotos de la misma categoría: los eventos de una operación anterior no deben actualizar la nueva pantalla; Tasks 2 y 4.
- Dos objetos de la misma categoría: mostrar ambos cuadros, un único LED y progreso por ubicación; Tasks 2 y 4.
- Un sensor cambia mientras otros objetivos siguen pendientes: conservar confirmaciones correctas y revertir solo el objetivo retirado; Task 2.
- Corrección después de que la foto caducó o con otro objeto seleccionado: rechazarla sin guardar la imagen completa con una etiqueta incorrecta; Task 3.

---

## Archivos y contratos

- `colab/kiwi_multiobject.py`: localización, recortes y respuesta del reconocedor principal; conservar `MultiObjectProbe` como alias para reproducir experimentos históricos.
- `colab/kiwi_vlm.py`: Qwen GPU o CPU cuantizada; conservar los criterios actuales de aceptación.
- `Kiwi_EmbeddingGemma2_Colab.ipynb`: arranque principal multiobjeto, páginas Gradio y REST. EmbeddingGemma pasa a ser verificación adicional, desactivada por defecto, sin sustituir el flujo DINO/Qwen.
- `pi/kiwi_pi/recognizer.py`: clientes REST/Gradio, validación de respuesta y simulador multiobjeto.
- `pi/kiwi_pi/app.py`: transformación de resultados, operación vinculada a foto, logging y correcciones por recorte.
- `pi/kiwi_pi/station.py`: guía simultánea, estados y sensores.
- `pi/kiwi_pi/static/{index.html,app.js,styles.css}`: cuadros, lista y progreso por ubicación, manteniendo la interfaz existente.
- `pi/config.example.yaml`, `README.md`, `pi/README.md`: configuración, tiempos de espera y documentación del flujo principal.

Contrato de Colab: `{mode: "grounding_dino_qwen", image_size: [width, height], objects: [{id, box, crop_box, detector_label, detector_score, category, recognized, recognition}], elapsed_seconds}`. `recognition` conserva `candidate`, `similarity`, `margin`, `reason` y `ranking` de Qwen.

Contrato de la Pi: `{photo_id, operation_id, status, image_size, objects, slots, reason, elapsed_seconds}`. Cada objeto incluye resultado aceptado/rechazado y ubicación, si procede. `status` es `accepted` si hay al menos un objeto aceptado, `rejected` si ninguno, o `error` si el reconocimiento no pudo completarse. `slots` contiene ubicaciones únicas, no una entrada por cuadro.

Contrato de guía: `{operation_id, phase, targets, pending, confirmed, wrong_slots, blocked, verify, remaining}`. `blocked` identifica objetivos ocupados al iniciar que aún no se han liberado. Los conjuntos se serializan como listas de IDs ordenadas.

### Task 1: Reconocedor principal y notebook de Colab

**Files:** `colab/kiwi_multiobject.py`, `colab/kiwi_vlm.py`, `Kiwi_EmbeddingGemma2_Colab.ipynb`, `colab/test_multiobject_probe.py`, `pi/tests/test_colab_contract.py`.

**Interfaces:** `MultiObjectRecognizer(classify).identify(image, threshold=.25)` devuelve `(annotated, gallery, response)`; `identify_objects(image)` devuelve solo `response` para REST. Ambos usan los mismos modelos y bloqueo.

- [x] Añadir pruebas que exijan `image_size`, conservación de todos los rechazos, lista vacía sin clasificación y máximo de 12 objetos. Verificar que el notebook usa DINO/Qwen por defecto y no responde mediante el antiguo clasificador si falta cargar DINO.
- [x] Ejecutar `rtk proxy env PYTHONPATH=pi:colab /tmp/kiwi-merge-venv/bin/python -m pytest colab/test_multiobject_probe.py pi/tests/test_colab_contract.py -q`; confirmar fallos por los contratos nuevos.
- [x] Implementar `MultiObjectRecognizer` y sus contratos en el módulo existente. Conservar alias del experimento y compartir bloqueo entre REST y Gradio.
- [x] Ajustar el notebook: categorías sin cargar obligatoriamente EmbeddingGemma, carga de Qwen y DINO principal, Gradio con imagen/galería/JSON y `/predict` con respuesta multiobjeto. `/health` reportará el modo principal y disponibilidad de ambos modelos. La verificación por referencias será adicional y explícita.
- [x] Añadir carga CPU NF4 a `colab/kiwi_vlm.py`, reutilizando la configuración del experimento CPU; GPU conserva el modelo Qwen3-VL-4B. Documentar la menor resolución de CPU.
- [x] Ejecutar las pruebas y validar la sintaxis de todas las celdas Python del notebook; commit de esta unidad.

### Task 2: LEDs y verificación simultánea por sensores

**Files:** `pi/kiwi_pi/station.py`, `pi/tests/test_station.py`, nuevo `pi/tests/test_multiobject_station.py`.

**Interfaces:** `begin_guides(slots: list[Slot], operation_id: str, test_mode: bool=False) -> None`; `snapshot()['guide']` entrega el contrato de guía anterior. `cancel_guide()` invalida el reconocimiento en curso y limpia todos los conjuntos.

- [x] Añadir pruebas de dos y tres objetivos en distintos órdenes, confirmación parcial, objetivo ya ocupado, retirada de uno confirmado, varios errores de ubicación, timeout parcial y sensores desactivados. Exigir fin solo con todos confirmados y ningún error pendiente.
- [x] Ejecutar `rtk proxy env PYTHONPATH=pi /tmp/kiwi-merge-venv/bin/python -m pytest pi/tests/test_multiobject_station.py -q`; confirmar fallos.
- [x] Sustituir el objetivo único por mapas/conjuntos de objetivos, pendientes, confirmados, bloqueados y equivocados. Evitar que las transiciones de un sensor borren el estado de otro.
- [x] Implementar azul por pendiente, verde por confirmado y rojo por posición equivocada. Un objetivo ocupado inicialmente requiere liberación antes de aceptar una nueva ocupación. Una retirada durante la operación revierte su confirmación.
- [x] Conservar el temporizador global de guía; al completar todos iniciar el reinicio automático. Timeout conserva el progreso visible y apaga todas las luces. Sin sensores no completar automáticamente.
- [x] Ejecutar las pruebas de sensores y actualizar las pruebas antiguas al contrato de guía; commit de esta unidad. La verificación de cancelaciones HTTP se completa en Task 3.

### Task 3: Contrato de la Pi y correcciones individuales

**Files:** `pi/kiwi_pi/recognizer.py`, `pi/kiwi_pi/app.py`, `pi/config.example.yaml`, `pi/tests/test_station.py`, nuevo `pi/tests/test_multiobject_api.py`.

**Interfaces:** `Recognizer.predict(jpeg)` entrega el contrato Colab; `Station.begin_guides(slots, operation_id, test_mode=False)` será implementado por Task 2. `RejectBody` añade `object_id`; cada objeto guardado incluye sus cuadros.

- [x] Crear pruebas de lista vacía, resultados mixtos, categoría sin ubicación, IDs repetidos, cuadro inválido y dos detecciones de la misma categoría. Exigir un único objetivo por ubicación y ningún LED para rechazados.
- [x] Crear pruebas de cancelación/respuesta atrasada y de corrección por `object_id`: el archivo guardado debe tener las dimensiones del recorte, no de la foto completa; foto caducada u objeto inexistente no deben guardar datos.
- [x] Ejecutar `rtk proxy env PYTHONPATH=pi /tmp/kiwi-merge-venv/bin/python -m pytest pi/tests/test_multiobject_api.py -q` y confirmar los fallos esperados.
- [x] Validar explícitamente el contrato remoto; actualizar el cliente Gradio para tomar el JSON final de su respuesta de tres salidas y el simulador para devolver varios objetos. Mantener el driver REST en `/predict`.
- [x] Transformar todos los resultados en `/api/recognize`, asignar `operation_id` único, registrar por objeto e iniciar `begin_guides` una sola vez. No reinterpretar un rechazo explícito de Qwen como aceptación de producción.
- [x] Corregir `/api/reject` para guardar el recorte validado del objeto elegido y sus metadatos. Guardar una corrección cancela la operación activa, como define el diseño.
- [x] Configurar `timeout_seconds: 1800` en el ejemplo para admitir la prueba CPU lenta; documentar que GPU permite reducirlo después de medir la latencia y que un túnel externo también puede imponer límites.
- [x] Ejecutar pruebas; commit de esta unidad usando el contrato de guía de Task 2.

### Task 4: Cuadros y progreso en la interfaz

**Files:** `pi/kiwi_pi/static/index.html`, `pi/kiwi_pi/static/app.js`, `pi/kiwi_pi/static/styles.css`, `pi/tests/test_ui_requests.cjs`, nuevo `pi/tests/test_ui_multiobject.cjs`.

**Interfaces:** Consume los contratos de respuesta y guía. Las correcciones envían `{photo_id, object_id, actual}`. Los eventos se aplican solo cuando `operation_id` coincide con la foto mostrada.

- [x] Añadir pruebas de coordenadas de cuadros expresadas en porcentaje según `image_size`, resultados mixtos, categorías duplicadas y eventos de otra operación con iguales categorías. La confirmación parcial no debe llamar a `resetToCapture`.
- [x] Ejecutar las pruebas Node y confirmar fallos por el comportamiento nuevo.
- [x] Mostrar fotografía anotada con cuadros y lista de objetos, incluidos rechazados. Dibujar sobre la imagen con dimensiones relativas para mantener alineación en celular y escritorio.
- [x] Adaptar el mapa a múltiples objetivos y mostrar «N de M ubicaciones confirmadas». Mostrar posiciones bloqueadas/equivocadas sin ocultar el progreso correcto; terminar solo con `phase: returned` de esta operación.
- [x] Añadir corrección por objeto y conservar controles de nueva foto/cancelación. Evitar insertar etiquetas remotas como HTML.
- [x] Ejecutar pruebas Node y comprobar en navegador el flujo con simulador: tres LEDs activos, una confirmación parcial, error en otra ubicación y finalización conjunta. Commit de esta unidad.

### Task 5: Verificación completa y documentación

**Files:** `README.md`, `pi/README.md`, `colab/MULTIOBJECT_PROBE.md`, documentos de diseño/plan y registros del experimento.

**Interfaces:** No añade contratos; verifica el recorrido completo de Tasks 1–4.

- [ ] Ejecutar `rtk proxy env PYTHONPATH=pi:colab /tmp/kiwi-merge-venv/bin/python -m pytest pi/tests colab/test_multiobject_probe.py -q` y las pruebas Node. Validar sintaxis de notebook y `rtk proxy git diff --check`.
- [ ] Revisar `/predict`, Gradio y la Pi con el mismo esquema de lista; confirmar que el README ya no pide una sola herramienta por fotografía como flujo principal.
- [ ] Documentar instalación, ejecución CPU/GPU, múltiples LEDs/sensores, categorías repetidas, correcciones por recorte y límites comprobados. Registrar resultados reales disponibles; no atribuir precisión ni latencia GPU sin medición.
- [ ] Solicitar una revisión independiente del cambio completo, resolver hallazgos y repetir únicamente las verificaciones afectadas.
- [ ] Integrar y publicar el cambio con README actualizado después de pasar las verificaciones y la revisión; informar qué sigue pendiente con hardware físico y fotos reales.
