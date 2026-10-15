# Kiwi local en Mac

Instalación probada en MacBook Pro M5 / 16 GB. Qwen3-VL-4B-Instruct en MLX 4 bits clasifica; Grounding DINO tiny en PyTorch MPS localiza. La Raspberry conserva pantalla, seguimiento, decisión y GPIO. El iPhone conserva su función de webcam.

## Arranque

En la instalación preparada, abrir `Iniciar_Kiwi_Mac.command` del escritorio o de la carpeta «Key week 3». En un clon nuevo, los lanzadores portables están en esta carpeta `mac/`: abrir [Iniciar_Kiwi_Mac.command](Iniciar_Kiwi_Mac.command) y [Detener_Kiwi_Mac.command](Detener_Kiwi_Mac.command). La preparación inicial y el ejemplo privado de configuración están en el [README principal](../README.md#preparar-el-mac-desde-el-repositorio). El lanzador evita duplicar el servidor, usa modelos descargados sin internet y evita la suspensión por inactividad con `caffeinate -i`. Mantener el Mac abierto y en el mismo Wi-Fi que la Pi. Para detenerlo, abrir `Detener_Kiwi_Mac.command`.

El servidor espera completar el calentamiento antes de anunciar que está disponible. La interfaz Raspberry muestra **Mac en línea**. Está seleccionado **Varios**; al pausar se puede alternar a **Rápido · 1**.

## Conexión instalada

- Raspberry: `10.74.11.201`; cámara: `https://10.74.11.201:8000/camera`.
- Mac: `10.74.10.134`; API: `https://10.74.10.134:8003/predict`.
- HTTPS firmado con la CA existente de la Pi. La clave privada de la CA permanece en la Pi; la clave privada del servidor Mac permanece en `mac/tls`, con permisos privados.
- API autenticada con la clave existente; configuración privada JSON en `mac/.env`, permisos 600, excluida de Git.
- El cliente Pi agrega su CA local a la verificación TLS predeterminada; no desactiva la validación.
- Si cambia la IP del Mac, actualizar tanto el endpoint de la Raspberry como el certificado SAN del servidor. El arranque de la Pi sí actualiza automáticamente su propia URL de cámara y QR.

## Rendimiento y confirmación

El límite del flujo Mac es de dos inicios de análisis por segundo, sin solicitudes de imagen acumuladas. DINO produce candidatos, el seguimiento exige estabilidad, y Qwen revisa hasta seis recortes estables en un ciclo, mediante lotes de hasta tres. Se valida la correlación de todos los resultados antes de dejar alguno provisional. Otro cuadro compatible confirma la posición; finalizar invalida cualquier trabajo pendiente.

Los objetos ya aceptados no se reclasifican continuamente. Se conservan los temporizadores independientes de 20 segundos. El modo de un objeto exige dos clasificaciones coincidentes.

La clasificación calcula probabilidades relativas entre las opciones, con mínimos de 0.90 y margen 0.50. Esos valores **no representan precisión calibrada**. Se incluyen categorías negativas y objetos sin posición; el servidor anuncia `calibrated: false`. No se entrenó un detector específico del laboratorio.

La imagen de benchmark es un montaje artificial de tres fotos de referencia: multímetro, servomotor y cautín. Las pruebas miden procesamiento, REST/HTTPS, seguimiento y salidas simuladas. No reemplazan una prueba con tres herramientas reales a 45°, y no verifican LEDs físicos (estaban desconectados).

Resultados detallados en `results/local-model-benchmark.json`, `results/local-end-to-end.json` y `results/pi-mac-benchmark.json`. Los resultados iniciales más lentos se conservan con sufijo `.initial.json` para no ocultar la diferencia entre primera ejecución y calentamiento.

## Compatibilidad

La API conserva `/health`, `/detect`, `/classify` y `/predict`. Agrega `/classify-batch` con máximo tres archivos y una correlación por archivo. El cliente Pi descubre esta capacidad en `/health`; Colab sin esa capacidad conserva el flujo de un recorte por ciclo. Cambiar de endpoint desactiva la capacidad por lote hasta recibir un estado nuevo.

Dependencias fijadas en `requirements.lock.txt`; entorno aislado `mac/.venv`. Modelos descargados:

- `mlx-community/Qwen3-VL-4B-Instruct-4bit`, snapshot `2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b`.
- `IDEA-Research/grounding-dino-tiny`, snapshot `a2bb814dd30d776dcf7e30523b00659f4f141c71`.

El cálculo evita proyectar todos los tokens al vocabulario: utiliza el estado del último token y la misma proyección final. El benchmark compara el resultado con el forward completo. MLX limita los buffers libres a 256 MiB y el presupuesto de evaluación a 6 GiB; mantiene hasta 4 GiB residentes (limitado también por la recomendación del dispositivo).

Referencias: [MLX-VLM](https://github.com/Blaizzy/mlx-vlm), [modelo MLX](https://huggingface.co/mlx-community/Qwen3-VL-4B-Instruct-4bit), [Grounding DINO](https://huggingface.co/docs/transformers/model_doc/grounding-dino), [memoria MLX](https://ml-explore.github.io/mlx/build/html/python/memory_management.html).

## Medición final desde la Raspberry

Después de reiniciar el servidor con preparación de modelos y memoria limitada, las cinco ejecuciones por HTTPS/Wi-Fi confirmaron las tres categorías correctas entre **4.216 y 4.922 segundos**, sin posiciones adicionales. Rápido · 1 confirmó el recorte de multímetro en **1.189 segundos**. GPIO simulado; montaje de referencias; no es validación de cámara real a 45° ni de LEDs físicos. El margen de la prueba más lenta respecto a cinco segundos es pequeño: otras escenas, red y carga del Mac pueden cambiar el tiempo.
