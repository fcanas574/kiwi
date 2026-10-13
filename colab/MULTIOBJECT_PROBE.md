# Prueba experimental de varios objetos

[Abrir notebook en Colab](https://colab.research.google.com/github/fcanas574/kiwi/blob/codex/grounding-dino-probe/Kiwi_Multiobject_Probe_Colab.ipynb).

Grounding DINO tiny localiza objetos mediante nombres en inglés. Se eliminan cuadros muy similares, se recorta cada detección con un margen de 4 % y Qwen3-VL-4B clasifica cada recorte. Los resultados incluyen ambos puntajes por separado, categoría, aceptación, cuadros y tiempos. Máximo 12 recortes por imagen.

1. Seleccionar GPU T4 y ejecutar las celdas en orden. El primer arranque descarga los modelos.
2. Revisar las tres fotos de referencia y el montaje artificial. Este montaje solo comprueba el flujo; no mide precisión en una escena real.
3. Subir una foto real con varias herramientas en la interfaz final. Revisar omisiones, cuadros repetidos, confusiones y tiempos.
4. Si la cuenta no puede conectar GPU, ejecutar solo el diagnóstico DINO en CPU al final. La categoría `pending_qwen` significa que Qwen no se ejecutó.

Esta prueba no usa la verificación opcional de EmbeddingGemma, no cambia `/predict` y no activa LEDs. Las puntuaciones no son probabilidades calibradas. Una estación y su cautín pueden producir detecciones separadas. Si DINO omite un objeto, Qwen no lo recibe.

Validación de código: tres pruebas verifican eliminación de duplicados, límites de cuadros, envío de cada recorte y conservación de rechazos. Para ejecutarlas: `PYTHONPATH=colab python -m pytest colab/test_multiobject_probe.py -q`.

Resultado del 13 de octubre de 2026: Colab rechazó la GPU por límite de uso. DINO se ejecutó en CPU sobre el montaje artificial y produjo 9 detecciones, 8 después de eliminar cuadros similares, en 27,643 segundos de inferencia. Encontró regiones sobre el multímetro, servo y cautín, junto con objetos de fondo y cuadros duplicados. Sus etiquetas mezclaron varias categorías en algunas regiones. Qwen no se ejecutó; la precisión del flujo completo sigue pendiente. [Resultados](probe_results/dino_cpu.json) y [visualización de los cuadros devueltos](probe_results/dino_cpu_boxes.jpg).

Implementación del detector basada en la [documentación oficial de Transformers](https://huggingface.co/docs/transformers/model_doc/grounding-dino).

## Ejecutar el flujo completo en CPU

Para una prueba lenta sin GPU, usar una sesión CPU nueva y ejecutar:

```python
%pip install -q -U transformers accelerate bitsandbytes
import urllib.request
source_url = 'https://raw.githubusercontent.com/fcanas574/kiwi/454739b/colab/kiwi_cpu_probe.py'
exec(compile(urllib.request.urlopen(source_url).read().decode(), 'kiwi_cpu_probe.py', 'exec'))
```

El experimento carga DINO, guarda sus cuadros y libera el detector antes de cargar Qwen3-VL-4B con cuantización NF4 de bitsandbytes. Reduce la resolución de los recortes a un máximo de 131.072 píxeles para limitar el costo. Mantiene los criterios de aceptación originales y no añade filtros de fondo. La cuantización y menor resolución pueden cambiar los resultados respecto al flujo GPU. Requiere descargar los pesos completos de Qwen la primera vez.

Guarda resultados parciales por recorte en `/content/kiwi_cpu_probe/results.json`, los recortes y la imagen anotada. Este experimento usa un montaje de fotos de referencia y no mide precisión sobre escenas reales ni datos independientes. La [documentación de bitsandbytes en Transformers](https://huggingface.co/docs/transformers/quantization/bitsandbytes) describe el soporte CPU y la carga en 4 bits.
