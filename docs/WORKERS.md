# Workers de GPU

Veyra puede ejecutar los trabajos pesados (entrenar, predecir sobre imágenes y las sesiones en vivo) en **otro equipo con GPU**.
El worker es un servicio aparte (repositorio `veyra-worker`); el protocolo está en `veyra-worker/docs/PROTOCOL.md`.

## Modo de ejecución (`LABELHUB_GPU_MODE`)

| Valor | Efecto |
|---|---|
| `local` (por defecto) | todo corre como subprocesos de este servidor; los workers se ignoran |
| `remote` | solo en workers registrados y en línea; sin ninguno, el trabajo falla con un motivo claro |
| `auto` | en un worker si hay uno en línea para ese tipo de trabajo; si no, en este servidor |

## Carpeta compartida

API y worker deben ver los archivos en la **misma ruta**: `LABELHUB_MEDIA_ROOT`, `LABELHUB_MODELS_ROOT`, `LABELHUB_RUNS_ROOT` y
`LABELHUB_EXPORT_ROOT` apuntan dentro de la carpeta que el worker tiene como `VEYRA_DATA_ROOT` (p. ej. `/data`, un NFS).
Los videos de los proyectos también deben estar ahí (la base guarda su ruta absoluta). El worker rechaza rutas fuera de esa carpeta.

## Alta de un worker

1. Gestión → Sistema → **Workers de GPU** (solo superusuarios) → «Registrar». El token se muestra una vez.
2. En el equipo con GPU: `.env` con `VEYRA_API_URL` y `VEYRA_WORKER_TOKEN`, `docker compose up -d`.
3. El worker aparece «En línea» con su GPU. Se puede revocar desde la misma tabla.

## Qué pasa si algo falla

- Un worker sin latidos durante `LABELHUB_WORKER_OFFLINE_AFTER` s (60 por defecto) se da por desconectado y sus trabajos en curso fallan.
- Si el worker se reinicia, lo que tenía en curso se marca como fallido.
- Si la API se reinicia, los trabajos remotos que nadie esperaba se cancelan (el worker los detiene).
- Cancelar un entrenamiento o detener una sesión llega al worker en su siguiente latido (~5 s).
- Detectar imágenes espera hasta `LABELHUB_WORKER_QUEUE_TIMEOUT` s (120) a que un worker tome el trabajo.

## Pruebas

`pytest tests/test_workers.py` simula al worker por HTTP. `python tests/ui/e2e_remote_worker.py` levanta una API de prueba y el
worker real con la GPU (entrena 1 época, detecta imágenes y corre una sesión en vivo).
