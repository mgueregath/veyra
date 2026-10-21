# Flujos de detección (O / Y)

Un **flujo** combina fuentes de detecciones de un proyecto: modelos entrenados, detectores del proyecto y **procedimientos** de su
espacio de trabajo. Se arma en la interfaz («Flujos…» junto al selector de detector) y se usa en:

- **Etiquetado asistido** (Gestión → Asistido): el flujo propone las cajas que luego se etiquetan; quedan marcadas con la fuente «flujo».
- **Ejecución sobre imágenes** (Modelos → Ejecutar modelo → Imágenes), del proyecto o subidas.
- **Evaluación** (Gestión → Evaluación): el flujo se mide como cualquier detector, con las mismas métricas.

- **Video y cámara en vivo** (Modelos → Ejecutar modelo → Video / Cámara): modelo **o** flujo, a elección y de forma independiente en cada fuente (imágenes, video, cámara).

> En vivo el flujo solo admite **modelos** y **procedimientos** (el detector de apariencia y las propuestas guardadas trabajan sobre imágenes sueltas). Se evalúa en cada fotograma: cada fuente usa su propio puntaje mínimo y el umbral de la pantalla no se aplica.

## Cómo se combinan

| Operador | Resultado |
|---|---|
| **O** (cualquiera) | Todas las detecciones de todas las fuentes; las que se solapan (IoU ≥ solape) se funden en una. |
| **Y** (todas) | Solo las detecciones que **todas** las fuentes confirman (una caja de cada una con IoU ≥ solape). Cada caja se usa una vez. |

Se pueden anidar grupos: «(modelo O manchas) Y filtro». Cada fuente trae su **puntaje mínimo** (en su propia escala: los modelos van de 0 a 1;
el detector de apariencia usa otra). Dos cajas solo se funden o coinciden si sus clases son compatibles (iguales, o alguna sin clase:
apariencia, movimiento y procedimientos sin `label` no distinguen clases). La caja resultante es el promedio ponderado por puntaje y el puntaje
combinado es el máximo (O) o la media (Y), configurable por grupo (`score`: `max` | `mean` | `min`).

## Fuentes

| Fuente | Qué es | Dónde corre |
|---|---|---|
| `model:<id>` | Modelo entrenado del proyecto | GPU (worker) |
| `baseline:appearance` | Detector de apariencia que declara el proyecto (`config.proposer`) | API |
| `proposals:motion` / `proposals:blob` | Propuestas ya guardadas en las imágenes del proyecto | base de datos |
| `procedure:<nombre>` | Función del repositorio del espacio de trabajo | contenedor aislado en el worker |

## Procedimientos (desde el espacio de trabajo)

En el repositorio git del espacio de trabajo, un `veyra.yaml` declara las funciones disponibles:

```yaml
procedures:
  - name: manchas
    entry: lice_motion.veyra:manchas      # módulo:función
    doc: Detector de manchas por apariencia
    params: {k: 8, min_score: 4.0}
```

La función recibe la imagen (BGR, numpy) y los parámetros, y devuelve `[{"bbox": [x, y, w, h], "score": 0.9, "label": "opcional"}]`.
Se ejecuta el **commit** elegido (por defecto el último; una etiqueta lo fija), en un contenedor sin red con el repositorio y los datos
en solo lectura: lo que no esté confirmado en git no se usa y cada resultado es reproducible. La traza guarda el commit exacto.
Dependencias: las del entorno virtual del proyecto (`pip install …` en el espacio de trabajo).

## API

`GET/POST /api/projects/{id}/flows`, `PUT/DELETE …/flows/{id}`, `GET …/procedures` (administración y revisión),
`POST …/detect` y `…/detect/upload` con `flow_id`, `POST …/preannotations` con `flow_id`, evaluación con `detector: "flow:<id>"`.
Especificación: `{"op": "and", "inputs": [{"source": "model:3", "min_score": 0.25}, {"op": "or", "inputs": [...]}], "iou": 0.3}`.

## En vivo

La sesión con flujo corre en el worker dentro de un contenedor de la imagen del espacio del proyecto (el mismo entorno virtual donde
se desarrollaron los procedimientos), con el repositorio y los datos en solo lectura y GPU. Se publica solo el puerto de la sesión.
A diferencia de los procedimientos por lotes, el contenedor **tiene red** (las cámaras la necesitan). Requiere un worker con
`workspace` entre sus tipos de trabajo; `POST …/live` acepta `flow_id` (excluyente con `model_id`).
