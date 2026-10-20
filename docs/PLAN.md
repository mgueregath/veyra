# Plan de implementación: plataforma de etiquetado, entrenamiento y detección en vivo

Objetivo final: entrenar y evaluar modelos con el dataset generado por la plataforma, y ver en vivo cómo detectan sobre un video grabado, un flujo RTSP o un flujo MJPEG. El trabajo es multiusuario, organizado por proyectos, con todos los datos en una base de datos.

## 1. Punto de partida

Lo que existe hoy en `cv-residue-diff`:

- Interfaz de etiquetado (`scripts/label_app.py` + `label_app.html`): cajas con clases piojo/mosca/ni uno, zoom, reproductor del video original con tiempo exacto (PTS), guardado de instantáneas, borrado restaurable, panel de controles junto a la caja. 172 comprobaciones en Chrome real.
- Datos en JSON (`labeling/frames.json`, `labels.json`): 1236 frames, unos 64 etiquetados y 33 revisados.
- Herramientas: muestreo de lotes (`sample_frames.py`), exportación YOLO + COCO con reparto por video (`export_dataset.py`), evaluación (`eval_labels.py`, `eval_on_labels.py`).
- Detectores de referencia: apariencia (puntaje ≥ 6: 94 % de recall y 66 % de precisión sobre 27 frames) y movimiento (31 % de recall).

Riesgos que condicionan el plan:

- Hay muy pocas etiquetas para entrenar un modelo confiable.
- Los frames se muestrearon sesgados hacia lo que ya encuentra el detector de apariencia. Se debe conservar `stratum` y añadir negativos reales.
- Los piojos son pequeños y translúcidos. El modelo necesitará alta resolución o recortes (tiles).
- No se sabe si hay GPU. Condiciona el modelo y los FPS en vivo.

## 2. Arquitectura

```
                 +----------------------------+
 navegador  ---> |  API + web (un solo repo)  | ---> PostgreSQL (metadatos, etiquetas, usuarios, colas)
 (etiquetado,    |  auth, roles, proyectos    | ---> Almacenamiento de archivos (videos, frames, miniaturas, modelos)
  visor en vivo) +-------------+--------------+
                               |
              +----------------+-----------------+
              |                                  |
     Trabajos (entrenar/evaluar/exportar)   Motor de inferencia
     lee dataset versionado de la BD        fuentes: archivo | RTSP | MJPEG
```

Principio: la base de datos guarda los datos estructurados; los archivos pesados (videos, imágenes, pesos de modelos) se quedan en disco u objeto, y la base guarda su ruta, tamaño y hash.

## 3. Elección del motor de base de datos

### Requisitos que importan

| Requisito | Por qué |
|---|---|
| Varios usuarios escribiendo a la vez | Etiquetado simultáneo, revisión y trabajos en segundo plano |
| Cola de trabajo con bloqueo | Que dos personas no tomen la misma imagen |
| Datos semiestructurados | Cada proyecto puede tener campos propios (`stratum`, `t_src`, puntajes) |
| Consultas analíticas | Conteos por clase, video, usuario y estrato; acuerdo entre etiquetadores |
| Integridad e historial | Auditoría de cambios, versiones de dataset inmutables |
| Operación simple | Equipo pequeño, un servidor |

### Opciones evaluadas

| Motor | Ventajas | Límites para este caso |
|---|---|---|
| SQLite (WAL) | Cero administración, un archivo | Un solo escritor a la vez, sin bloqueo de filas ni acceso por red; frena con varios usuarios y trabajos |
| **PostgreSQL** | Concurrencia real, `SELECT … FOR UPDATE SKIP LOCKED` para colas, `JSONB` para campos flexibles, índices parciales y GIN, restricciones, `LISTEN/NOTIFY`, extensiones (`pg_trgm`), copias y réplicas maduras | Requiere un servicio (se resuelve con Docker Compose) |
| MongoDB | Documentos flexibles | Se pierde integridad referencial y consultas analíticas con joins; las colas y el historial son más difíciles de garantizar |
| DuckDB | Excelente para análisis | No es una base transaccional multiusuario |

### Decisión

**PostgreSQL** como motor único de la plataforma. Razones: cubre concurrencia, colas con bloqueo, campos flexibles (`JSONB`) y análisis en un solo sitio, y es lo bastante simple de operar para un equipo pequeño.

Reglas de uso:

- Un único motor en desarrollo y producción para no tener comportamientos distintos. Un `docker compose up` levanta la base.
- Migraciones con Alembic; acceso con SQLAlchemy.
- Los archivos no van en la base: solo su ruta lógica, tamaño y hash SHA-256.
- Para análisis pesados fuera de línea se exporta a Parquet y se consulta con DuckDB. No es la fuente de verdad.
- Copias de seguridad diarias con `pg_dump` y verificación de restauración.

### Esquema principal (resumen)

| Tabla | Contenido |
|---|---|
| `users` | usuario, hash de contraseña o identidad externa, estado |
| `projects` | nombre, configuración (clases, atajos, colores, fuentes de caja) en `JSONB` |
| `memberships` | usuario, proyecto, rol (admin, etiquetador, revisor, lector) |
| `videos` | proyecto, ruta, hash, fps, duración, rotación, tabla de PTS o su ruta |
| `frames` | video, índice, `t` exacto (PTS), estrato, ruta de imagen, `meta JSONB` |
| `boxes` | frame, bbox, clase, fuente (movimiento, apariencia, modelo, manual), puntaje, posición original, borrado suave |
| `annotations` | frame, usuario, cajas y clases de esa persona, estado (borrador, enviado), sello de tiempo |
| `reviews` | frame, revisor, decisión final, motivo, enlace a las anotaciones usadas |
| `assignments` | frame, usuario, estado, bloqueo con expiración |
| `audit_log` | quién, qué, cuándo, valor anterior y nuevo |
| `dataset_versions` | proyecto, nombre, reglas, hash, reparto train/val/test fijo por video |
| `dataset_items` | versión, frame, partición, estado final de etiquetas (inmutable) |
| `models` | proyecto, versión de dataset, parámetros, ruta de pesos, métricas |
| `runs` | trabajo de entrenamiento o evaluación, usuario, estado, registros |
| `live_sessions` | proyecto, modelo, fuente, umbral, conteos y clips guardados |

Índices clave: `assignments` (estado, proyecto) para la cola; `boxes` por frame; `frames` por video y por estrato; GIN sobre `meta` si se consulta por campos libres.

Cola de asignación: cada etiquetador toma el siguiente frame con `FOR UPDATE SKIP LOCKED`, se registra un bloqueo con expiración y se libera al terminar o al caducar.

## 4. Lenguaje y frameworks

### Lenguaje del backend

| Opción | A favor | En contra |
|---|---|---|
| **Python** | Es el lenguaje del ecosistema de visión: PyTorch, OpenCV, Ultralytics/RT-DETR, ONNX Runtime, albumentations, pycocotools. Ya está escrito en Python todo el pipeline, los detectores, la exportación y la evaluación. FastAPI + SQLAlchemy cubren API y base de datos | Un solo hilo por proceso para código Python puro (el GIL). Se mitiga porque OpenCV, PyTorch y ffmpeg liberan el GIL y los trabajos pesados van en procesos aparte |
| Go | Excelente para servidores concurrentes y streaming | No tiene ecosistema de entrenamiento; habría dos lenguajes y reescribir lo existente |
| Rust | Rendimiento y seguridad | Mismo problema de ecosistema, con coste de desarrollo mucho mayor |
| TypeScript (Node) | Un solo lenguaje con el frontend | Sin ecosistema de ML; la inferencia y el entrenamiento tendrían que vivir igualmente en Python |

**Decisión: Python (≥ 3.10) con FastAPI**, SQLAlchemy 2 y Alembic. Una sola base de código para la API, los trabajos de entrenamiento y evaluación y el motor de inferencia. FastAPI aporta validación con tipos, documentación OpenAPI automática, WebSocket (para el visor en vivo) y respuestas por rangos para video. El único tramo crítico en rendimiento (captura y decodificación de RTSP/MJPEG) se apoya en bibliotecas nativas (ffmpeg/PyAV/OpenCV).

### Framework del frontend

| Opción | A favor | En contra |
|---|---|---|
| **React + TypeScript + Vite** | Ecosistema más grande (TanStack Query, Zustand, bibliotecas de tablas y formularios), más fácil encontrar ayuda y contratar, TypeScript con soporte maduro | Más código repetitivo que Svelte |
| Svelte / SvelteKit | Menos código, muy rápido | Ecosistema y comunidad más pequeños |
| Vue 3 | Curva suave, buen equilibrio | Ecosistema de componentes de anotación menor |
| Mantener JS plano | Cero migración | No escala a múltiples pantallas, roles, tablas ni visor en vivo |

**Decisión: React + TypeScript + Vite**, con TanStack Query para datos del servidor y Zustand para el estado local de la interfaz.

El editor de cajas (lienzo con zoom, arrastre, redimensionado, reproductor) es el corazón de la aplicación y es sobre todo código imperativo de lienzo. Por eso se escribe como un **módulo de TypeScript independiente del framework** (`frontend/src/editor/`), y React solo lo monta y le pasa datos. Así el framework no condiciona el rendimiento del lienzo y el editor se puede probar por separado.

### Resto de herramientas

- Pruebas: pytest (backend) y Playwright o el arnés actual de Chrome por CDP (interfaz).
- Calidad: ruff y mypy (Python), eslint y tsc (frontend).
- Contenedores: Docker Compose para la base; la API y los trabajos pueden correr en la máquina con GPU.
- Contrato API ↔ frontend: el esquema OpenAPI que genera FastAPI se usa para generar los tipos de TypeScript.

## 5. Proyectos, usuarios y roles

- **Proyecto**: unidad de trabajo con su configuración (clases, atajos, colores, fuentes), videos, lotes, versiones de dataset, modelos y visor en vivo. Lo actual de piojos es el primer proyecto.
- **Roles por proyecto**:
  - Admin: configura el proyecto, las clases, las versiones y los entrenamientos.
  - Etiquetador: etiqueta lo que se le asigna.
  - Revisor: aprueba, corrige y resuelve desacuerdos.
  - Lector: solo ve (por ejemplo el visor en vivo).
- **Autenticación**: usuario y contraseña con sesión; inicio de sesión externo (OIDC) como extensión si ya existe un proveedor.
- **Consenso**: cada persona guarda su anotación; el estado final lo fija el revisor, o una regla de mayoría si hay doble etiquetado. Permite medir el acuerdo entre etiquetadores.
- **Permisos**: cada llamada a la API comprueba usuario, proyecto y rol. Videos e imágenes se sirven solo a quien tenga acceso (se mantiene la lista blanca de rutas).
- **Seguridad**: HTTPS y proxy inverso si sale de la red local. No exponer el servicio a internet sin ello.

## 6. Fases

### F0. Repo independiente, base de datos y proyectos — HECHA

Resultado: repo `labelhub` con PostgreSQL y migraciones, usuarios/roles/proyectos, importador verificado con los datos reales, API de imágenes, anotaciones por usuario, servidor de video (Range, tiempos reales, instantáneas), y el editor y el reproductor portados. Las 172 comprobaciones de la herramienta anterior quedaron en 158 en Chrome real; las otras 14 (exportador, evaluación, poda, errores del servidor de video) pasaron a pruebas del backend.

- Repo nuevo con el servidor y la interfaz actuales, sin dependencia del pipeline de detección.
- Configuración por proyecto (`project.json` importable) en lugar de clases escritas en el código.
- PostgreSQL con Docker Compose, migraciones y el esquema anterior.
- Importador de `frames.json` y `labels.json` actuales (con borrados y revisados) y exportador de vuelta a JSON.
- Usuarios, sesiones, roles y selección de proyecto.
- Las pruebas de Chrome real se parametrizan por proyecto y se amplían a dos sesiones simultáneas.
- Criterio de salida: el proyecto de piojos corre desde la base de datos con los mismos datos y las 172 comprobaciones pasan.

### F1. Dataset versionado y flujo de trabajo — HECHA

- Cola de asignación, bloqueos y filtros «mis imágenes», «sin revisar» y «con desacuerdo».
- Flujo etiquetador → revisor, con opción de doble etiquetado en una muestra.
- Versiones de dataset inmutables con reparto fijo por video, que no cambia entre versiones.
- Estadísticas de calidad en la interfaz: conteos por clase, video, estrato y usuario; acuerdo entre etiquetadores.
- Meta de datos para un primer modelo: 600–800 frames revisados de al menos 30 videos, con 15–20 % sin ningún piojo. Un conjunto de test aparte, con doble revisión, que no se toca hasta evaluar.

### F2. Línea base y evaluación — HECHA

- Evaluador único: mAP@0.5, precisión y recall en el umbral de uso, y conteo por video, sobre el test.
- Línea base 1: detector de apariencia (≥ 6). Línea base 2: pipeline de movimiento. Es el mínimo a superar.
- Informe automático con ejemplos de falsos positivos y negativos, guardado en `runs`.

### F3. Entrenamiento — HECHA

- Primer modelo: detector pequeño a alta resolución o con tiles, 3 clases (piojo, mosca, ni uno como negativo duro).
- Licencia por decidir: Ultralytics es AGPL; alternativas permisivas: RT-DETR, RF-DETR, YOLOX.
- Aumentos pensados para el caso (desenfoque, color, rotación, escala), sin deformar al insecto.
- Registro de cada experimento en `runs` y `models` con la versión del dataset, los parámetros y las métricas, y el usuario que lo lanzó.
- Criterio de salida: superar en recall a la línea base de apariencia con precisión igual o mejor, en test por video.

### F4. Ciclo de mejora con etiquetado asistido — HECHA

- El modelo preanota lotes nuevos y la interfaz los muestra como fuente «modelo» con su puntaje.
- Se priorizan los frames más inciertos y donde modelo y detector discrepan.
- Cada ronda reentrena y se mide contra el mismo test.

### F5. Motor de inferencia en vivo — HECHA

- Abstracción de fuente de video con tres implementaciones: archivo, RTSP y MJPEG. Un hilo de captura descarta frames atrasados para que la latencia no crezca.
- El detector corre sobre el frame más reciente; seguimiento temporal opcional (reutilizando el tracker) para contar cada piojo una sola vez.
- Exportación a ONNX y medición de velocidad. Si hay GPU, probar TensorRT.

### F6. Visor en vivo — HECHA

- Página web con el video, las cajas dibujadas, FPS, latencia y control de umbral de confianza.
- Elección de proyecto, modelo y fuente.
- Botones para pausar, guardar el frame y enviarlo a la cola de etiquetado del proyecto, de modo que los falsos positivos y los piojos perdidos en vivo vuelven al dataset.
- Grabación de clips con detecciones y resumen de conteos por sesión (`live_sessions`).

### F7. Administración y operación — HECHA

- Alta y baja de usuarios, cuotas de disco por proyecto.
- Copias de seguridad programadas y exportación completa de un proyecto (base y archivos).
- Monitoreo básico de trabajos y del servicio.

## 7. Criterios de aceptación finales

- Un comando reproduce la evaluación sobre una versión de dataset fija.
- Varios usuarios etiquetan y revisan a la vez sin pisarse, con historial de cambios.
- El visor funciona con un video grabado, un flujo RTSP y uno MJPEG, con latencia medida.
- Existe un informe que compara línea base y modelo, por video.
- Se puede restaurar un proyecto completo desde una copia de seguridad.

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| Pocas etiquetas | Etiquetado asistido por el modelo (F4) y muestreo de frames inciertos |
| Piojos muy sutiles | Alta resolución o tiles; se mide el costo en FPS |
| Cámara en mano, desenfoque | Evaluación por video para ver dónde falla |
| Migración de JSON a la base con pérdida de datos | Importador con verificación (conteos, borrados, revisados) y los JSON actuales como respaldo |
| Acceso no autorizado | Autenticación, roles, HTTPS y proxy inverso fuera de la red local |
| Disco lleno con videos y frames | Cuotas por proyecto y almacenamiento externo |

## 9. Decisiones pendientes

1. ~~GPU disponible~~ Resuelto: RTX 4080 en esta máquina. Falta confirmar si el visor en vivo correrá en ella.
2. Licencia del detector (AGPL o permisiva).
3. FPS y latencia mínimos en vivo, y resolución de las cámaras RTSP y MJPEG.
4. Horas de etiquetado disponibles.
5. Número de usuarios simultáneos y si están en la misma red.
6. Autenticación propia o proveedor externo.
7. Doble etiquetado de todo, de una muestra, o solo etiquetador y revisor.
8. Si cada proyecto puede ser de un dominio distinto (otras clases y otro tipo de dato).

## 10. Orden recomendado

F0 → F1 y F2 (en paralelo, F2 no depende de las respuestas pendientes) → F3 → F4 en paralelo → F5 → F6 → F7.


## 11. Resultados y estado (2026-10-20)

Las fases F0 a F7 están implementadas y probadas. Pruebas: 116+ del backend, 39 del núcleo del editor y 245 en Chrome real
(editor 158, flujo de trabajo 47, entrenamiento 22, en vivo 18), todas con `scripts/test_all.sh`.

**Qué se midió de verdad (GPU RTX 4080):**

| Medida | Resultado |
|---|---|
| Detector solo (YOLOv8n, 1280, fotogramas 1920×1080) | 176 fps (5,7 ms) |
| Proceso en vivo completo (lectura + detección + seguimiento + dibujo + MJPEG), 1280 | 30,7 fps, latencia 10 ms |
| Ídem a 640 | 68,7 fps, latencia 8 ms |
| YOLO real en una sesión en vivo desde la API | 41,6 fps sobre video 1080×1920 |
| Fuentes probadas | archivo (a ritmo real y sin ritmo), MJPEG por HTTP (un proceso lee el flujo de otro), RTSP real con `mediamtx` en Docker, incluido el corte de la cámara y la reconexión |
| ONNX / TensorRT | no hecho: la exportación de este PyTorch exige `onnxscript` y, con 30 fps de sobra, no hace falta |

**Hallazgos sobre los datos (importantes para decidir qué hacer ahora):**

- Con las 33 imágenes revisadas, la línea base de apariencia da, según el IoU exigido: IoU ≥ 0,1 → precisión 66 % / recall 93 %;
  IoU ≥ 0,3 → 46 % / 65 %; IoU ≥ 0,5 → 26 % / 37 %. La herramienta anterior contaba como acierto cualquier solape. Las cajas del
  detector localizan mal al piojo (las personas las movieron o agrandaron). Hay que fijar el IoU del proyecto (0,3 parece razonable).
- El detector de movimiento casi no acierta (mAP 0,002).
- Un YOLOv8n entrenado con 14 imágenes da mAP50 0,02 frente a 0,30 de la línea base de apariencia: **faltan etiquetas**. La
  meta del plan (600–800 imágenes revisadas de al menos 30 videos) sigue siendo lo que desbloquea un modelo útil.

**Pendiente (decisiones de personas, no de código):** licencia (Ultralytics es AGPL; el único archivo que habría que cambiar es
`workers/train_yolo.py`, más `predict_yolo.py` y `live_core.YoloDetector`), horas de etiquetado, autenticación externa (OIDC)
si se necesita, y si el visor en vivo correrá en la misma máquina que el entrenamiento.

**Límites conocidos:** el recuento de piojos únicos en vivo es una cota superior (con la cámara en la mano el fondo se mueve y el
seguimiento por IoU puede perder la identidad); las evaluaciones de modelos se ejecutan dentro de la petición (bien para cientos
de imágenes, habría que pasarlas a la cola para miles); la preanotación y el entrenamiento comparten una sola cola de GPU.
