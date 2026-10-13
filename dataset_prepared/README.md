# Dataset preparado para EmbeddingGemma 2

Las 44 fotos se inspeccionaron visualmente. Los originales de `Kiwi Dataset` permanecen intactos; aquí se guardan copias etiquetadas. El conjunto sirve como referencias para embeddings, sin fine-tuning ni entrenamiento por refuerzo.

## Conteos

| Categoría | Referencias | Evaluación | Total |
|---|---:|---:|---:|
| `soldering_station` | 4 | 0 | 4 |
| `digital_multimeter` | 9 | 1 | 10 |
| `bench_power_supply` | 4 | 0 | 4 |
| `servo_motor` | 4 | 0 | 4 |
| `oscilloscope` | 5 | 1 | 6 |
| `function_generator` | 4 | 1 | 5 |
| `variable_capacitor` | 3 | 0 | 3 |
| `capacitors` | 2 | 0 | 2 |
| `power_adapter` | 4 | 0 | 4 |
| `multimeter_box` | 5 | 0 | 5 |
| `solder` | 2 | 0 | 2 |
| `otros` | 3 | 0 | 3 |

Las 11 categorías se conservan en la configuración. El reto requiere seleccionar 9; no se quitó ninguna categoría para anticipar esa selección.

## Fotos de prueba añadidas (12/10/2026)

Ocho fotos tomadas con el teléfono durante la primera prueba de la estación se añadieron a `references.zip` (49 fotos):
- `digital_multimeter_007`–`010`: multímetro INGCO amarillo (frontal, lateral y trasera). Antes se confundía con `multimeter_box` o `bench_power_supply`.
- `solder_002`: cautín con mango rojo.
- `otros`: **referencias negativas** (papel toalla, alicate, Arduino Nano). Si el objeto se parece más a `otros`, la estación lo rechaza. Añadir aquí objetos comunes del taller que no deben encender un LED.

Para comprobar la mejora hay que usar fotos **nuevas**: las fotos añadidas ya no sirven como prueba.

## Etiquetas confirmadas

- `multimeter_box`: cinco cajas de cartón amarillas/naranjas de multímetros AstroAI. Son empaques y se mantienen como una clase propia, separada de `digital_multimeter`.
- `solder`: una foto de cautín eléctrico con mango y punta. Esta etiqueta se usa por elección del usuario para el objeto *soldering iron*; en inglés, *solder* suele referirse al material de soldadura.
- `power_adapter`: cuatro adaptadores AC/DC con cable integrado. No se detectó un cable de alimentación suelto.

## Evaluación y duplicados

`evaluation.zip` contiene tres fotos: un multímetro, un generador de funciones y un osciloscopio. `references.zip` contiene 41 fotos y no incluye evaluación. No se detectaron duplicados binarios; hay varias vistas del mismo equipo desde ángulos distintos, así que las fotos de evaluación pueden compartir equipo o modelo con las referencias.

## Uso en Colab

1. Copiar el contenido de `colab_categories.py` a una celda del notebook existente; conserva las 11 etiquetas configuradas.
2. Ejecutar la sección 4 para reconstruir las descripciones y vectores de texto.
3. En la sección 5, activar la carga de referencias y seleccionar `references.zip` (41 fotos).
4. Probar `evaluation.zip` (3 fotos) como evaluación; no incorporarlo como referencias.
5. Elegir 9 categorías para el reto y ajustar umbrales con las referencias elegidas.

El notebook remoto no se modificó. Consulta `manifest.csv` para las rutas de origen/destino, confianza visual y notas por imagen.
