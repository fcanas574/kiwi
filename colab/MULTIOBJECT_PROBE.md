# Prueba experimental de varios objetos

[Abrir notebook en Colab](https://colab.research.google.com/github/fcanas574/kiwi/blob/codex/grounding-dino-probe/Kiwi_Multiobject_Probe_Colab.ipynb).

Grounding DINO tiny localiza objetos mediante nombres en inglés. Se eliminan cuadros muy similares, se recorta cada detección con un margen de 4 % y Qwen3-VL-4B clasifica cada recorte. Los resultados incluyen ambos puntajes por separado, categoría, aceptación, cuadros y tiempos. Máximo 12 recortes por imagen.

1. Seleccionar GPU T4 y ejecutar las celdas en orden. El primer arranque descarga los modelos.
2. Revisar las tres fotos de referencia y el montaje artificial. Este montaje solo comprueba el flujo; no mide precisión en una escena real.
3. Subir una foto real con varias herramientas en la interfaz final. Revisar omisiones, cuadros repetidos, confusiones y tiempos.
4. Si la cuenta no puede conectar GPU, ejecutar solo el diagnóstico DINO en CPU al final. La categoría `pending_qwen` significa que Qwen no se ejecutó.

Esta prueba no usa la verificación opcional de EmbeddingGemma, no cambia `/predict` y no activa LEDs. Las puntuaciones no son probabilidades calibradas. Una estación y su cautín pueden producir detecciones separadas. Si DINO omite un objeto, Qwen no lo recibe.

Validación de código: tres pruebas verifican eliminación de duplicados, límites de cuadros, envío de cada recorte y conservación de rechazos. Para ejecutarlas: `PYTHONPATH=colab python -m pytest colab/test_multiobject_probe.py -q`.

Implementación del detector basada en la [documentación oficial de Transformers](https://huggingface.co/docs/transformers/model_doc/grounding-dino).
