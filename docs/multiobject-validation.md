# Validación de la integración multiobjeto

13 de octubre de 2026. Flujo principal: foto → Grounding DINO → recortes → Qwen3-VL-4B → ubicaciones aceptadas → verificación simultánea por sensores.

## Resultados

- 48 pruebas de Python aprobadas: reconocedor, contratos Colab/REST/Gradio, API, correcciones individuales, sensores, cancelaciones y resultados atrasados.
- 6 pruebas de JavaScript aprobadas: cuadros adaptables, progreso parcial, ubicaciones repetidas, aislamiento por operación y respuestas atrasadas.
- Sintaxis válida en las 11 celdas de código del notebook. Diferencias sin errores de formato.
- Navegador con simulador: tres ubicaciones encendidas, confirmación parcial, ubicación equivocada y finalización conjunta. Corrección por objeto, cierre con Escape y restauración del foco. Pantalla de 390 × 844 sin desbordamiento horizontal.

## Revisión independiente y correcciones

La revisión de `e94ce15..bd5f8a4` encontró dos problemas importantes. Ambos se reprodujeron con cinco pruebas fallidas antes de corregirlos en una única pasada:

1. Completar la guía durante un muestreo ignoraba retiradas o errores procesados después del último objetivo. El monitor aplica todos los cambios aceptados y la estación evalúa la finalización al terminar el lote. Las pruebas cubren retirada simultánea y ocupación simultánea de una ubicación equivocada.
2. Un recorte incompatible podía guardar toda la foto o una región disjunta como ejemplo de un objeto. Se exigen coordenadas enteras de píxel y un recorte que contenga su cuadro sin exceder el margen del reconocedor (4 %, mínimo 2 píxeles). Se rechaza la respuesta antes de activar LEDs si incumple este contrato.

## Decisiones y límites

- Se usó un worktree administrado para mantener intacto el checkout original durante la implementación.
- Se conservaron los eventos de botones en JavaScript externo. El auditor estático de interfaz reportó 11 falsos positivos de botones sin acción al no resolver esos eventos; el recorrido en navegador sí los verificó. La auditoría estática estricta no se declara aprobada.
- Un aviso de deprecación de Starlette/httpx apareció en las pruebas y no impidió su ejecución.
- Las pruebas de integración usan modelos y hardware simulados. Falta medir el notebook completo con modelos reales en GPU y validar LEDs/sensores físicos y fotos de varios objetos.
- Los sensores confirman presencia por ubicación, no cantidad ni identidad. No se añadieron filtros del fondo, según el alcance aprobado.
- La publicación en `main` está incluida en el plan aprobado por el usuario; no requiere una nueva aprobación del mismo paso.
