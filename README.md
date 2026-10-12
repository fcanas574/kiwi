# Kiwi — identificación de herramientas de electrónica

Prototipo para reconocer un objeto presentado ante una cámara y asociarlo con su ubicación de almacenamiento. Utiliza **EmbeddingGemma 2** para comparar una fotografía con descripciones o con fotos de referencia, y **Gradio** para la página del celular y la API.

[![Abrir en Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/fcanas574/kiwi/blob/main/Kiwi_EmbeddingGemma2_Colab.ipynb)

## Contenido

- [Notebook](Kiwi_EmbeddingGemma2_Colab.ipynb): instalación, conexión a Drive, carga del modelo, referencias y página/API con contraseña.
- [Contexto del reto](Key_Week_Proyecto_Sistema_Inteligente_Herramientas.md): objetivos, requisitos y cronograma.
- [Dataset preparado](dataset_prepared/README.md): 44 fotos etiquetadas, **41 referencias** y **3 fotos de evaluación**.
- [Referencias ZIP](dataset_prepared/references.zip) y [evaluación ZIP](dataset_prepared/evaluation.zip).
- [Manifiesto](dataset_prepared/manifest.csv): relación entre archivos originales, copias, categorías y notas. Las rutas son relativas a la raíz del repositorio.
- `Kiwi Dataset/`: fotografías originales; `dataset_prepared.zip`: paquete completo de los datos organizados.

## Usar en Colab

1. Abrir el notebook con el botón de arriba y guardar una copia en Drive.
2. Seleccionar GPU T4 si está disponible. También se puede probar con CPU, con mayor latencia.
3. Descargar `dataset_prepared/references.zip` y guardarlo en **Mi unidad → Kiwi → references.zip** de Google Drive.
4. Ejecutar las celdas en orden y autorizar la conexión a Drive. Si se actualiza una biblioteca que ya estaba en memoria y aparece un error de importación, reiniciar la sesión y ejecutar nuevamente.
5. La sección 5 carga las fotos desde Drive. El resultado del reconocimiento debe mostrar `mode: image_to_image`.
6. Introducir una contraseña de al menos 12 caracteres en la sección 6. Abrir el enlace temporal de Gradio desde el celular con usuario `kiwi` y esa contraseña. Subir una foto con un objeto y pulsar **Identificar**.

Colab y el enlace de Gradio son temporales. Las restricciones de Colab gratuito pueden impedir usar el notebook principalmente como servicio web externo; revisar sus condiciones antes de usarlo como servidor de demostración. No ofrece disponibilidad permanente.

## Categorías actuales

`soldering_station`, `digital_multimeter`, `bench_power_supply`, `servo_motor`, `oscilloscope`, `function_generator`, `variable_capacitor`, `capacitors`, `power_adapter`, `multimeter_box` y `solder`.

En este proyecto, **`solder` identifica el cautín (`soldering iron`)**, por elección del equipo; no identifica alambre de soldadura. El reto exige elegir nueve elementos, mientras el dataset actual conserva once categorías para las pruebas.

## Mejorar el reconocimiento

Añadir fotos de los objetos reales en sus carpetas correspondientes, incluyendo vistas que causen confusión. Conservar fotos nuevas para evaluación, generar otra vez `references.zip` y ejecutar la sección 5. Las referencias deben incluir todas las categorías configuradas.

No se realiza entrenamiento por refuerzo ni fine-tuning. Se utiliza un modelo preentrenado y similitud entre embeddings. Los vectores se calculan al cargar las referencias y quedan en memoria: **todavía no se genera un archivo `reference_embeddings.npz`**.

`MIN_SIMILARITY` y `MIN_MARGIN` empiezan sin calibrar. Por eso se muestran candidatos con `recognized: false`. La similitud no es un porcentaje de confianza. Definir umbrales usando pruebas con objetos conocidos, desconocidos y mesa vacía antes de activar una señalización física. Cambiar las referencias requiere validar de nuevo esos umbrales.

Las tres imágenes de evaluación permiten una comprobación inicial; no bastan para afirmar precisión por categoría ni generalización a equipos nuevos.

## API y señalización

Gradio genera el endpoint `identify` y documenta sus clientes en **Use via API**. Devuelve candidato, similitud, margen, ranking y `location_id`. Este último queda vacío hasta configurar `LOCATION_BY_CATEGORY` con posiciones reales.

El microcontrolador y los LEDs todavía no están implementados. El cliente que los controle deberá activar una luz solo con `recognized: true` y una ubicación válida; ante rechazo o error, deberá apagar la señalización anterior.

## Fuentes

- [EmbeddingGemma 2: guía multimodal](https://ai.google.dev/gemma/docs/embeddinggemma/multimodal-embeddinggemma-with-sentence-transformers)
- [Modelo oficial](https://huggingface.co/google/embeddinggemma-2)
- [Gradio: enlaces y API](https://gradio.app/guides/sharing-your-app)
- [Condiciones de Colab](https://research.google.com/colaboratory/faq.html)

Los pesos del modelo se descargan desde su fuente oficial y no se incluyen en este repositorio.
