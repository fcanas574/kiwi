# Kiwi: reconocimiento y devolución simultánea de herramientas

## Objetivo acordado

El flujo principal será fotografía → Grounding DINO → recortes → Qwen3-VL-4B → lista de objetos → LEDs de todas las ubicaciones aceptadas → confirmación independiente por sensores. El usuario quiere devolver varias herramientas simultáneamente, sin seleccionar una por vez. No será una modalidad opcional ni una prueba separada del flujo de la estación.

La prueba CPU existente y sus resultados permanecen como evidencia del experimento. Esta integración conserva las categorías y el hardware configurado actualmente, y no añade todavía reglas para excluir el fondo.

## Reconocimiento en Colab

- Grounding DINO tiny localizará objetos con las descripciones de las herramientas en inglés.
- Se conservará la eliminación de cuadros muy similares y el máximo de 12 recortes del experimento. Qwen clasificará cada recorte y podrá rechazarlo individualmente como `otros` o por ambigüedad.
- La respuesta de `/predict` será una lista `objects`, con identificador, cuadro, categoría, aceptación, puntuación de DINO, puntuación y margen de Qwen, motivo y tiempo. Se incluirá el tamaño de la imagen orientada para dibujar correctamente los cuadros.
- Las puntuaciones de los dos modelos permanecerán separadas y no se presentarán como probabilidades calibradas.
- La página Gradio mostrará la fotografía anotada, los recortes y los resultados del mismo reconocedor que usa REST. El cliente Gradio de la Pi se adaptará a su nueva respuesta.
- El notebook principal incorporará este flujo por defecto. El uso de CPU cuantizada se documentará como configuración de ejecución lenta, conservando Qwen3-VL-4B. El experimento CPU actual no sustituye las pruebas de precisión con fotos reales.
- Las solicitudes Gradio y REST compartirán un bloqueo para evitar inferencias simultáneas sobre los modelos. Se devolverán errores claros si falta cargar uno de ellos; no se responderá silenciosamente con el antiguo clasificador de imagen completa.

## API de la estación

- `/api/recognize` procesará todos los objetos y evaluará cada uno con las categorías y ubicaciones configuradas.
- Un objeto rechazado no impedirá guiar los demás aceptados. Si ninguno es aceptado, todas las luces permanecerán apagadas y se mostrará el motivo.
- La guía se iniciará una sola vez, después de evaluar la lista completa, con todas las ubicaciones aceptadas.
- Los objetos sin ubicación configurada se mostrarán como no disponibles para esta estación y no activarán LEDs.
- Una foto nueva, cancelación o corrección invalidará la solicitud anterior; una respuesta atrasada no podrá reactivar LEDs.
- La operación tendrá un identificador vinculado a la foto. La interfaz relacionará los eventos de sensores con esa operación, no solo con una categoría.
- El simulador devolverá listas de objetos para probar el flujo completo sin Colab ni hardware real.
- El tiempo de espera de reconocimiento será configurable y se documentará para CPU y GPU; el límite actual de 30 segundos no sirve para la prueba CPU completa.

## LEDs y sensores simultáneos

La estación mantendrá conjuntos de ubicaciones objetivo, pendientes, confirmadas y equivocadas.

1. Al iniciar, todos los objetivos pendientes se encenderán en azul.
2. Cada sensor confirmará su ubicación de manera independiente y su LED cambiará a verde; los otros seguirán pendientes.
3. Una ubicación ocupada al iniciar no se contará como una nueva devolución. Deberá detectarse su liberación y posterior ocupación.
4. Si una ubicación confirmada se vacía mientras la operación sigue activa, volverá a pendiente y su LED a azul.
5. Ocupar una ubicación ajena a los objetivos que estaba vacía encenderá su señal de error. Al liberarla desaparecerá ese error, sin perder el progreso de las ubicaciones correctas.
6. La operación finalizará cuando todas las ubicaciones objetivo estén confirmadas y no queden errores de ubicación. Solo entonces comenzará el reinicio automático.
7. Al vencer el tiempo de guía se apagarán las luces y la pantalla mostrará qué ubicaciones quedaron pendientes. Cancelar o iniciar otra foto también apagará todas las luces.
8. Con sensores desactivados se mostrarán las ubicaciones, pero no se declarará una devolución verificada.

### Objetos repetidos y límite del hardware

La configuración actual asigna una única ubicación y un sensor de presencia a cada categoría. Varias detecciones de la misma categoría se mostrarán individualmente, pero compartirán un LED y una comprobación de presencia. No se afirmará que ese sensor verifica la cantidad de objetos o la identidad de la herramienta depositada. Verificar varias unidades de una categoría requeriría sensores o ubicaciones adicionales.

## Pantalla y correcciones

- La pantalla de resultado mostrará la foto con cuadros y una lista de objetos aceptados y rechazados.
- El mapa del estante mostrará simultáneamente ubicaciones pendientes, confirmadas y equivocadas. El progreso será por ubicaciones, por ejemplo «2 de 3 ubicaciones confirmadas».
- El botón principal permitirá iniciar otra fotografía o cancelar la operación actual. No se requerirá seleccionar un objeto para encender sus LEDs.
- La corrección de una identificación será por objeto: se elegirá el cuadro correspondiente y se guardará su recorte con la etiqueta correcta. Una imagen con varias herramientas no se añadirá completa a una sola categoría del dataset.
- El estado «completo» dependerá de los sensores; recibir categorías de Qwen no equivaldrá a devolver las herramientas.

## Validación necesaria

- Pruebas de Colab/API: lista vacía, mezcla de aceptados/rechazados, tamaño de imagen y cuadros, reconocedor principal activo y cliente Gradio actualizado.
- Pruebas de estación: dos y tres objetivos en cualquier orden, devolución parcial, ocupación previa, retirada de un objetivo confirmado, ubicaciones equivocadas simultáneas, cancelación, timeout y respuesta atrasada.
- Pruebas de correcciones: guardar solo el recorte elegido, sin contaminar referencias con el resto de la fotografía.
- Pruebas de interfaz: dibujar cuadros a distintas dimensiones de pantalla y mantener progreso por ubicación sin reiniciar al confirmar solo una herramienta.
- Recorrido completo con reconocimiento y hardware simulados, seguido de fotos reales con varios objetos. Los drivers físicos y la precisión en GPU se informarán como pendientes hasta verificarlos.

## Entrega

Actualizar el notebook principal, código Colab, API/cliente de la Pi, lógica de guía, interfaz, simulador, pruebas y README como una integración coherente. Las páginas y los ejemplos documentarán el reconocimiento multiobjeto como el flujo principal.
