# Kiwi: escaneo continuo desde un celular en un stand

Fecha: 13 de octubre de 2026. Estado: especificación aprobada por el usuario; implementación y corrección final verificadas por software, pendientes de revisión enfocada y validación física.

## Propósito y criterios de éxito

Un celular dedicado, colocado en un soporte ajustable y conectado al cargador, observa una zona de presentación de herramientas. El usuario presenta uno o varios objetos sin tomar fotos manualmente. Kiwi reconoce herramientas, enciende sus ubicaciones y conserva cada guía hasta que su sensor confirma la devolución. El escaneo permanece activo al terminar y admite nuevas herramientas sin borrar el progreso de las anteriores.

El éxito consiste en reducir las consultas repetidas a Qwen, mantener atendidos los LEDs y sensores mientras Colab trabaja y evitar que cuadros repetidos o respuestas atrasadas reinicien las devoluciones. No se promete una tasa de reconocimiento hasta medirla en GPU y en la Raspberry real.

## Decisiones acordadas

- Celular dedicado y Raspberry Pi 4 B en la misma red Wi-Fi. La memoria instalada se verificará al desplegar; se considera el equipo de 8 GB indicado por el usuario.
- Página web del celular mediante HTTPS local, con certificado confiado una vez en ese teléfono. No se requiere una app móvil ni un túnel adicional para acceder a la Raspberry.
- DINO frecuente y Qwen selectivo, ambos en Colab. La Raspberry realiza seguimiento geométrico ligero y decide cuándo consultar Qwen.
- Qwen recibe un recorte cuando el objeto está estable; se conserva su clasificación mientras el seguimiento sea válido.
- La desaparición de una herramienta no cancela su guía. Una categoría confirmada requiere ausencia observada antes de volver a activar una devolución.
- Pausar el escaneo conserva las guías. Cancelar devoluciones es una acción separada.
- No se incorpora análisis local de nitidez en esta versión. La estabilidad geométrica no garantiza nitidez ni identidad.

Los valores numéricos y reglas operativas siguientes concretan esas decisiones para revisión. Son parámetros iniciales ajustables, no resultados medidos.

## Arquitectura y responsabilidades

| Componente | Responsabilidad | Dependencias |
|---|---|---|
| Página del celular | Cámara trasera, vista previa, captura JPEG reducida y presentación de estado | HTTPS, permiso de cámara, Raspberry |
| Coordinador de escaneo en la Pi | Sesión, cadencia, solicitudes asíncronas y descarte de trabajo atrasado | Clientes Colab, seguimiento, estación |
| Seguimiento en la Pi | Asociar cuadros, comprobar estabilidad y decidir consultas a Qwen | Detecciones validadas y reloj monotónico |
| Colab/DINO | Localizar las categorías actuales y devolver cuadros | Modelo DINO existente |
| Colab/Qwen | Clasificar exclusivamente un recorte solicitado | Qwen3-VL-4B y criterios actuales |
| Estación en la Pi | Guías acumuladas, LEDs, sensores y confirmaciones | Drivers existentes |

La página habla con la Raspberry bajo el mismo origen HTTPS. La clave de Colab permanece en la Pi. Colab sigue accesible mediante el transporte REST existente, actualmente ngrok; HTTPS local no sustituye ese enlace.

No se envía una transmisión de video completa a Colab: el celular mantiene su vista previa y entrega imágenes recientes para DINO. Una clasificación envía únicamente el recorte elegido por la Pi. El coordinador no ejecuta modelos en la Raspberry.

La separación DINO/Qwen se hace reutilizando el reconocedor existente. Se conserva `/predict` como composición de ambas operaciones para el flujo manual y Gradio, sin mantener dos implementaciones del reconocimiento.

## Captura y planificación del trabajo

- Vista previa solicitada a 15–30 FPS; el navegador puede entregar una frecuencia distinta.
- Capturas para detección limitadas inicialmente a una por segundo y 960 píxeles en el lado mayor, conservando proporciones. Estos valores no son una garantía de rendimiento.
- Una única operación remota de inferencia de escaneo en curso por estación. El siguiente cuadro se captura cuando el coordinador pueda utilizarlo. No existe una cola creciente de imágenes.
- Tras cada detección se puede clasificar un objeto estable. Si hay varios, se alterna una detección reciente con una clasificación; se prioriza el objeto estable que más tiempo lleva esperando, evitando que uno bloquee a los demás.
- Colab conserva un bloqueo compartido de modelos para REST y Gradio. Qwen puede interrumpir la cadencia de DINO; esta versión no promete ejecución simultánea sobre la GPU.
- Las operaciones HTTP son asíncronas. Decodificación, redimensionado y generación de recortes se ejecutan fuera del bucle que atiende sensores y eventos cuando puedan bloquearlo.
- Se conservan como máximo 12 detecciones por imagen, el cuadro en uso y sus recortes necesarios. Los cuadros de escaneo no se añaden automáticamente al dataset ni al almacén histórico de fotos manuales.

## Seguimiento geométrico y estabilidad

DINO devuelve posiciones, tamaños, etiquetas propuestas y puntuaciones; no entrega identidad persistente. El seguimiento usa coordenadas normalizadas, por lo que cambiar la resolución no altera las tolerancias.

1. Se asocian detecciones con seguimientos recientes por superposición del cuadro (IoU), con asignación uno a uno y mínimo inicial de 0.3. La etiqueta de DINO puede ayudar, pero no sustituye la clasificación de Qwen.
2. Si dos asociaciones tienen puntuaciones demasiado próximas (diferencia menor que 0.1), se marca la asociación como ambigua y se reinicia su estabilidad. No se transfiere una clasificación mediante una asociación ambigua.
3. Un objeto se considera estable tras tres detecciones válidas consecutivas que abarquen al menos un segundo. Respecto al cuadro de referencia, cada una debe tener IoU de al menos 0.6, variación de área no mayor al 15 % y desplazamiento del centro no mayor al 2 % de la diagonal de la imagen.
4. Una detección faltante reinicia la evidencia de estabilidad, pero no elimina inmediatamente un seguimiento: se toleran hasta dos muestreos válidos sin asociación. Tras el tercero se retira el seguimiento, manteniendo cualquier guía ya activada.
5. Un intervalo superior a diez segundos entre detecciones invalida la continuidad geométrica. La configuración CPU lenta puede aumentar este límite para pruebas; no representa escaneo en tiempo real.
6. Cada seguimiento tiene un identificador y revisión geométrica. Como Qwen ocupa el turno de inferencia, su respuesta queda provisional hasta la siguiente detección DINO: solo se aplica si pertenece a la generación actual, el seguimiento aún existe y el nuevo cuadro sigue siendo compatible con el cuadro clasificado según las tolerancias de estabilidad. Una respuesta para un objeto retirado, cambiado o ambiguo se descarta. Si falta la asociación, se permite recuperarla dentro de los dos muestreos tolerados; al tercero se descarta la respuesta. Ningún LED nuevo se activa a partir de una clasificación provisional.

La geometría puede confundir herramientas intercambiadas en la misma posición, o una rotación que conserva el cuadro. Se documenta este límite y se mide en pruebas reales; no se presenta el seguimiento como identificación visual infalible.

## Clasificación selectiva, rechazo y rearme

El seguimiento tiene estados de observación, estabilidad, clasificación en curso, clasificación aceptada o rechazo. Solo existe una solicitud Qwen en curso para cada seguimiento.

- Una clasificación aceptada se reutiliza mientras la asociación sea válida. Varias detecciones de la misma categoría comparten una ubicación, como en el sistema actual.
- Un rechazo no activa LEDs ni se reintenta continuamente sobre el mismo cuadro estable. Se permite otra clasificación después de un cambio geométrico significativo y nueva estabilidad, con un intervalo mínimo de cinco segundos entre intentos del mismo seguimiento.
- Una categoría pendiente no se vuelve a añadir por otros cuadros o por la creación de otro seguimiento.
- Una categoría confirmada queda suprimida para nuevas devoluciones hasta que todos sus seguimientos conocidos hayan desaparecido durante tres detecciones válidas consecutivas. Pausas, errores de red y ausencia de cuadros no cuentan como evidencia de ausencia.
- Si todavía hay otras devoluciones pendientes en el ciclo, una categoría ya confirmada no se añade de nuevo a ese ciclo, incluso si desapareció y reapareció. Puede participar en el siguiente ciclo cuando también cumpla la condición de ausencia.
- Si se pierde el seguimiento de un objeto aceptado y aparece otro ambiguo, se consulta nuevamente Qwen tras estabilidad. Esto no borra ni duplica la guía existente.
- No se identifica cantidad ni identidad de las herramientas depositadas: un sensor verifica presencia en una ubicación.

## Guía continua y sensores

La sesión de cámara y el ciclo de devoluciones tienen identificadores distintos. Una sesión puede abarcar muchos ciclos. Un ciclo empieza con la primera categoría aceptada y mantiene su `operation_id` mientras se incorporan nuevas ubicaciones.

- Añadir objetivos no llama a `cancel_guide()` ni vuelve a inicializar todos los sensores: conserva pendientes, confirmados, bloqueados y errores existentes.
- Un objetivo nuevo se enciende azul. Si su sensor ya está ocupado al incorporarlo, requiere liberación y posterior ocupación antes de confirmar.
- Confirmación independiente en verde. La retirada de un objetivo confirmado mientras el ciclo siga activo lo devuelve a pendiente, como en el comportamiento actual.
- Se conserva la evaluación de todos los cambios de un muestreo de sensores antes de finalizar el ciclo, incluyendo ubicaciones equivocadas.
- Cuando todos los objetivos están confirmados y no existen errores de ubicación, se muestra el éxito y se cierra el ciclo después del período visual existente. El escaneo permanece activo. Una nueva categoría elegible durante ese período puede iniciar otro ciclo sin que el temporizador anterior lo cancele.
- Las categorías del ciclo cerrado permanecen suprimidas hasta cumplir el rearme. El fin de un ciclo no borra esas reglas.
- En escaneo continuo no se apagan guías por el timeout global de fotos: se conserva cada guía hasta confirmación o cancelación explícita. El tiempo transcurrido puede mostrarse como aviso. El flujo manual mantiene su timeout actual.
- Sin sensores habilitados se muestran ubicaciones pero no se declara una devolución verificada.

Cancelar devoluciones pausa también el escaneo e invalida solicitudes pendientes, para evitar que un resultado atrasado reactive LEDs. El usuario puede iniciar el escaneo de nuevo de forma explícita. Pausar por sí solo no cancela objetivos ni deshabilita sensores.

## Sesiones, fallos y reconexión

Una sola sesión de captura controla la estación. Otro navegador puede consultar el estado, pero no desplaza automáticamente al celular activo; cambiar de dispositivo requiere pausar o liberar esa sesión.

Cada captura incluye sesión, generación y número de cuadro creciente. Las respuestas repiten estos identificadores. Reiniciar, pausar, cancelar o sustituir el teléfono cambia la generación, por lo que los resultados anteriores no tienen efectos.

- Pérdida de cámara, bloqueo del celular o página oculta: se pausa la captura, se libera el trabajo pendiente y se muestra que el escaneo está detenido. Se solicita evitar suspensión cuando el navegador lo permita; no se garantiza captura en segundo plano.
- Pérdida de conexión con el teléfono: se conservan guías y sensores. Al volver, se recupera el estado de la Pi y se continúa con una nueva generación y cuadros nuevos; no se reinicia el ciclo de devoluciones.
- Error de Colab: se conserva el progreso y se reintenta con espera creciente de 1, 2, 4, 8 y hasta 15 segundos. Un éxito restablece la espera. No se acumulan peticiones durante la caída.
- Error de una clasificación: vuelve a elegible tras una nueva detección compatible y el intervalo de reintento; no produce aceptación provisional.
- Reinicio de la Raspberry: LEDs apagados, escaneo pausado y sensores nuevamente inicializados. No se restauran confirmaciones desde memoria volátil ni se presume una devolución ocurrida durante el reinicio.

La falta de observaciones tras una desconexión no rearma categorías confirmadas. Las supresiones y guías conservadas por la Pi sobreviven a la reconexión del navegador mientras el servidor permanezca activo.

## Contratos que se incorporarán

| Interfaz | Entrada y salida | Efecto |
|---|---|---|
| Colab `POST /detect` | JPEG y correlación de cuadro → tamaño, cuadros enteros, etiquetas DINO y tiempos | Ejecuta solo DINO |
| Colab `POST /classify` | JPEG del recorte y correlación de seguimiento → categoría, aceptación, ranking y tiempos | Ejecuta solo Qwen |
| Pi `/api/scan/start` y `/api/scan/pause` | Identidad de sesión → sesión, generación y estado | Controla captura, conservando guías al pausar |
| Pi `/api/scan/frame` | Sesión, generación, secuencia y JPEG → detecciones y resultados de ese cuadro | Valida y procesa un cuadro; rechaza otro si ya está ocupado |
| Estado y eventos de la Pi | Estado de escaneo y contrato de guía existente ampliado | Separa cámara, clasificación y progreso de devolución |
| Preparación y envío de corrección por seguimiento | Sesión, seguimiento y revisión → identificador de corrección; luego identificador y etiqueta elegida | Congela y guarda solo el recorte asociado validado |

Los identificadores de detección son locales a un cuadro; no se confunden con los identificadores de seguimiento. La Pi deriva el recorte a partir del cuadro validado, con margen de 4 % y mínimo de dos píxeles. Se mantiene validación de tamaño de imagen, coordenadas enteras, puntuaciones finitas y categorías coherentes antes de alterar LEDs o guardar correcciones.

El estado añade como mínimo estado de captura, sesión, generación, último cuadro procesado, seguimientos con revisión/estabilidad/clasificación, error de conexión y tiempos DINO/Qwen. La guía mantiene su propio `operation_id`. Los cuadros se dibujan sobre la imagen exacta asociada a la respuesta, no sobre un cuadro de video posterior, para evitar anotaciones desalineadas.

Las solicitudes de escaneo reutilizan el cliente REST y su autenticación. El modo manual `/predict` y Gradio siguen disponibles para pruebas y fotos individuales; una foto manual no reemplaza una guía continua activa sin cancelación explícita.

## HTTPS local y operación del stand

Se reserva una dirección IP de la Raspberry en el router. El acceso será por esa dirección HTTPS, con un certificado que incluya la IP en sus nombres válidos y esté firmado por una autoridad local. Se instala y habilita la confianza de esa autoridad en el celular dedicado una vez. La clave privada permanece en la Raspberry y no se añade al repositorio.

La instalación debe proporcionar creación del certificado, configuración del servidor HTTPS y una guía de confianza para Android/iOS. Se verifica el acceso real a `getUserMedia`, no solo que desaparezca un aviso del certificado. La compatibilidad se declara para el teléfono y navegador probados, no para cualquier celular sin preparación.

La página requiere permiso de cámara, permanece en primer plano y ofrece un QR con la dirección local. Se conserva el diseño visual actual de Kiwi. El stand debe sostener una vista fija de la zona de presentación; no se incluye diseño mecánico ni adquisición del soporte.

Se limita la captura a la sesión activa mediante una credencial de emparejamiento configurada en la Pi. La credencial de Colab no se expone al navegador. La documentación contempla renovación de certificado y actualización de la URL de Colab tras reiniciar su sesión.

## Interfaz y correcciones

- Vista previa, imagen analizada con cuadros y estados «observando», «esperando estabilidad», «clasificando», «reconocido» o «no reconocido».
- Mapa y progreso de ubicaciones conservados mientras llegan imágenes nuevas.
- Controles separados para iniciar/pausar escaneo y cancelar devoluciones; no hay reinicio automático de la cámara al confirmar un objetivo.
- Corrección por objeto sobre un recorte congelado, ligado a seguimiento y revisión. Preparar la corrección valida el objeto, pausa la captura y asocia atómicamente el recorte congelado a la nueva generación mediante un identificador de corrección. Así la pausa invalida inferencias anteriores sin invalidar la propia corrección. Se conservan las guías y se evita que cambie el objeto seleccionado.
- Una corrección válida guarda únicamente ese recorte y sus metadatos; invalida la clasificación elegida y cancela el ciclo de devolución para evitar mantener una guía equivocada. El escaneo permanece pausado hasta que el usuario lo reanude.
- Un recorte caducado o perteneciente a otra generación se rechaza sin guardar datos. La sesión conserva hasta 12 recortes del último cuadro analizado, sustituidos al recibir el siguiente. Preparar una corrección conserva una única copia adicional durante cinco minutos o hasta enviarla/cancelarla. La sesión no guarda todas las imágenes continuamente.
- Mantener navegación por teclado, foco visible, cierre con Escape y disposición móvil existente.

## Alcance y límites

Incluye cámara web continua, coordinación de escaneo, seguimiento geométrico, separación de endpoints Colab, guías acumuladas, rearme, reconexión, HTTPS local y documentación de despliegue. El trabajo se integra con categorías, drivers y criterios de aceptación actuales.

Quedan fuera: ejecución de modelos en la Pi, transmisión WebRTC, app móvil nativa, múltiples cámaras activas, identificación individual de herramientas iguales, análisis local de nitidez, entrenamiento nuevo, filtros nuevos de fondo y funcionamiento del reconocimiento sin Colab. La zona de presentación ayuda físicamente a controlar el fondo; no sustituye una regla de filtrado.

## Validación y aceptación

1. Seguimiento: cuadros estables y móviles, cambios de tamaño, asociaciones ambiguas, pérdidas breves, intervalos largos y cambio de resolución. No consultar Qwen antes de estabilidad; no repetirlo durante seguimiento aceptado.
2. Clasificación: rechazo sin luces, reintento tras movimiento, varios objetos elegibles sin inanición y respuesta Qwen que llega después de cambiar o retirar el objeto.
3. Guía: añadir objetivos sin perder progreso, varias herramientas de una categoría con un LED, ocupación previa, retirada de confirmados y cambios simultáneos de sensores.
4. Rearme: herramienta aún visible tras confirmar, ausencia válida de tres detecciones, ausencia por fallo de red que no rearma y nuevo ciclo tras la confirmación conjunta.
5. Sesiones: un emisor activo, pausa, cancelación, reconexión y resultados de otra generación. Memoria y solicitudes pendientes acotadas durante una caída prolongada.
6. API y correcciones: cuadros inválidos, recortes incompatibles, IDs inconsistentes, recorte caducado y aislamiento del modo manual respecto de una guía continua.
7. Navegador en el celular dedicado: certificado confiado, cámara trasera, QR, permisos, primer plano, pausa, bloqueo/desbloqueo, recuperación de Wi-Fi y cuadros dibujados sobre la imagen correcta.
8. Raspberry real: comparar CPU, memoria y cadencia de sensores con escaneo apagado/encendido; introducir latencia de Colab y medir respuesta de LEDs y sensores. Meta inicial: no sumar más de 100 ms al procesamiento de un evento ya antirreboteado frente al modo sin escaneo, sin perder cambios. Reportar mediciones, no inferir cumplimiento de una simulación.
9. GPU y fotos reales: medir tiempo DINO, tiempo Qwen por recorte, frecuencia efectiva y tiempo hasta el primer LED; registrar errores de clasificación y de seguimiento con uno y varios objetos. Si el stand no cumple la estabilidad geométrica, ajustar parámetros y documentar sus límites.

La implementación debe mantener aprobadas las pruebas actuales del modo manual y añadir pruebas independientes del hardware para el coordinador, el seguimiento y las guías continuas. La validación física y móvil permanece pendiente hasta disponer del equipo; no se considera cubierta por el simulador.

## Estado de validación al 13 de octubre de 2026

La implementación de software cubre la coordinación de escaneo, guías acumuladas, devolución por sensores, pausa/reconexión y descarte de respuestas atrasadas. La prueba integrada y la comprobación enfocada de navegador están registradas en [la validación del escaneo continuo](../../continuous-stand-validation.md). La revisión final de la rama identificó dos fallos de integración, resueltos en la corrección final autorizada; su revisión independiente enfocada sigue pendiente.

El recorrido simulado confirmó que un sensor puede completar su tick mientras DINO sigue pendiente, que solo una clasificación Qwen confirmada por una detección DINO posterior activa la guía, y que añadir otro destino conserva el progreso existente. Las suites disponibles de software y las comprobaciones de sintaxis pasaron.

La validación de hardware y servicio real no se realizó: no hay Raspberry Pi, teléfono dedicado ni GPU de Colab conectados. La confianza de CA y la cámara trasera en el teléfono, QR real, carga y tiempos GPU, CPU/RAM/GPIO, cadencia efectiva y la meta física de ≤100 ms permanecen pendientes. No se infieren cifras de pruebas simuladas ni se declara cumplimiento físico.

La auditoría estática premium conservó 15 errores `affordance.actionless-button` y 0 avisos; la herramienta no reconoce los manejadores de JavaScript, así que el resultado no se registra como un pase. El comportamiento de los controles cubiertos sí se comprobó en navegador. Task 6 tampoco generó ni decodificó una imagen QR real porque `qrencode` no estaba disponible.

Referencias: [diseño multiobjeto actual](../../multiobject-integration-design.md), [validación anterior](../../multiobject-validation.md), [requisitos de cámara del navegador](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).
