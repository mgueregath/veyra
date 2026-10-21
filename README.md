# Veyra

> Antes «LabelHub». El nombre del repositorio y las variables `LABELHUB_*` se mantienen por compatibilidad.

Plataforma multiusuario, organizada por proyectos, para **etiquetar** imágenes de video con cajas, construir **datasets
versionados**, **entrenar y evaluar** detectores, y verlos funcionar **en vivo** (video grabado, RTSP o MJPEG).
Nació de la herramienta de etiquetado de `cv-residue-diff` (detección de piojos) y es agnóstica al dominio: las clases, atajos y
colores viven en la configuración de cada proyecto.

Plan y resultados medidos: [docs/PLAN.md](docs/PLAN.md).

## Qué hace

| Área | Funciones |
|---|---|
| Etiquetado | Editor de cajas (mover, redimensionar, crear, clases, borrar y restaurar), panel de etiqueta junto a la caja, reproductor del video original (±5 s, tiempo exacto, pasos de un fotograma, velocidad, instantáneas), carrusel, atajos |
| Equipo | Proyectos, roles (administrador, revisor, etiquetador, lector), cola de trabajo con bloqueo atómico, reparto con doble etiquetado, flujo etiquetador → revisor, acuerdo entre personas |
| Datos | Versiones de dataset inmutables con reparto train/val/test **por video**, exportación YOLO y COCO |
| Modelos | Evaluación (mAP, precisión, recall, F1, conteo por video, ejemplos de errores) de líneas base y modelos; entrenamiento YOLO en la GPU con cola, registro y cancelación; preanotación con un modelo y búsqueda de las imágenes más inciertas |
| Carga de datos | Gestión → Cargar datos: imágenes sueltas o .zip, con o sin etiquetas (YOLO: .txt + classes.txt/data.yaml; COCO: .json), y videos (mp4, mov, m4v, avi, mkv, webm) muestreados de forma automática (intervalo, inicio y final) o manual (reproduciendo y tomando instantáneas) |
| En vivo | Sesiones sobre un video o una cámara RTSP/MJPEG: flujo con cajas, velocidad y latencia, umbral, pausa, grabación de clips, recuento, y «guardar fotograma» que vuelve al etiquetado |
| Operación | Gestión del equipo, uso de disco y cuota, estado del servicio, copia y restauración de un proyecto, copia verificada de la base |

## Arquitectura

| Capa | Tecnología |
|---|---|
| API | Python ≥ 3.10, FastAPI, SQLAlchemy 2, Alembic |
| Base de datos | PostgreSQL 16 (metadatos, etiquetas, usuarios, colas); los archivos pesados quedan en disco |
| Frontend | React + TypeScript + Vite; el editor de cajas es un módulo independiente del framework |
| Procesos de trabajo | `workers/`: entrenamiento, predicción y detección en vivo corren **aparte**, con un Python que tenga PyTorch; la API solo los orquesta |

El detector en vivo escucha solo en `127.0.0.1` con un token: la API autentica a las personas y reenvía el flujo.

## Puesta en marcha

```bash
docker compose up -d                       # PostgreSQL propio en 127.0.0.1:55433
python3 -m venv .venv && . .venv/bin/activate
pip install -e "backend[dev]"
cp .env.example .env                       # y ajustar
cd backend && alembic upgrade head         # crea el esquema
python -m app.cli createuser admin --superuser
python -m app.cli import-legacy /ruta/a/cv-residue-diff/labeling --videos-dir /ruta/a/cv-residue-diff/videos
python -m app.cli copy-annotations legacy admin     # quien siga etiquetando recibe las etiquetas importadas
cd ../frontend && npm install && npm run build
cd ../backend && uvicorn app.main:app --host 0.0.0.0 --port 8910   # sirve la API y el frontend
```

`import-legacy` solo **lee** `frames.json` y `labels.json` y se puede repetir sin duplicar datos. Las etiquetas importadas quedan
a nombre del usuario «legacy». Para desarrollar el frontend: `npm run dev` (proxy a la API en :8910).

### Variables de configuración

Ver [.env.example](.env.example). Las importantes: `LABELHUB_SECRET_KEY`, `LABELHUB_MEDIA_ROOT`, `LABELHUB_TRAIN_PYTHON`,
`LABELHUB_PRETRAINED_DIRS`, `LABELHUB_LIVE_ALLOWED_HOSTS`.

## Línea de comandos

| Comando (`python -m app.cli …`) | Para qué |
|---|---|
| `createuser`, `addmember` | usuarios y roles |
| `import-legacy`, `copy-annotations` | migrar desde la herramienta anterior |
| `set-config` | configuración de un proyecto (clases, fuentes de caja, detector de preanotación, cuota) |
| `export-dataset` | exportar una versión a YOLO/COCO |
| `restore-project` | crear un proyecto nuevo desde una copia descargada de la pantalla Sistema |

Copia de seguridad de la base (vuelca, restaura en una base temporal y compara las tablas): `scripts/backup_db.sh [carpeta] [cuántas]`.

## Pruebas

```bash
scripts/test_all.sh                        # todo: lint, tipos, backend, editor y las cuatro suites en Chrome real
```

| Suite | Qué cubre |
|---|---|
| `backend/` (pytest, base PostgreSQL aparte `labelhub_test`) | API, permisos, concurrencia de la cola, importador, evaluación, entrenamiento (con procesos simulados), en vivo (archivo, MJPEG, RTSP con Docker), administración |
| `frontend/` (vitest) | geometría y modelo del editor, cola de guardado |
| `tests/ui/test_editor.py` | 158 comprobaciones del editor y el reproductor, con videos reales (port de las 172 de la herramienta anterior) |
| `tests/ui/test_workflow_ui.py` | cola, bloqueo, estados, revisión, gestión, datasets, evaluación, equipo, sistema |
| `tests/ui/test_training_ui.py` | entrenamiento, modelos, evaluación de un modelo, preanotación asistida |
| `tests/ui/test_live_ui.py` | visor en vivo, controles, guardar fotograma, cámara caída, URL peligrosa, vista de lector |

Cada suite crea su propia base y su propio servidor en un puerto libre: no toca los datos de trabajo.

## Seguridad y límites

- Las contraseñas se guardan con scrypt; las sesiones son cookies firmadas. Para salir de la red local: HTTPS y un proxy inverso.
- Quien no pertenece a un proyecto recibe 404 (no se revela que exista). Las rutas de archivos salen siempre de la base de datos,
  nunca de la petición.
- Las URL de cámara solo admiten `rtsp(s)://` y `http(s)://` (FFmpeg también lee `file:` o `concat:`); las contraseñas de la URL no se
  devuelven. Solo administradores y revisores pueden iniciar sesiones en vivo; `LABELHUB_LIVE_ALLOWED_HOSTS` limita los destinos.
- Los procesos de trabajo cargan pesos con `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` (necesario con Ultralytics 8.2 y PyTorch ≥ 2.6): **nunca
  apuntarlos a pesos de origen desconocido**.
- Ultralytics es AGPL-3.0: decisión de licencia pendiente (ver el plan).
- El recuento de piojos únicos en vivo es una cota superior (cámara en la mano).

## Estado

Fases F0 a F7 del plan implementadas y probadas. Lo que queda no es código sino datos y decisiones: **más imágenes revisadas** (con 14
de entrenamiento un YOLOv8n no supera a la línea base de apariencia), fijar el IoU del proyecto y resolver la licencia.

La interfaz anterior (`cv-residue-diff/scripts/label_app.py`) sigue funcionando sin cambios; `legacy/` guarda una copia de referencia.

## GPU en otro equipo

Los trabajos pesados pueden ejecutarse en un worker remoto: ver [docs/WORKERS.md](docs/WORKERS.md).

## Flujos de detección

Modelos, detectores y procedimientos del espacio de trabajo combinados con O / Y para el asistido, la ejecución y la evaluación: ver [docs/FLUJOS.md](docs/FLUJOS.md).
