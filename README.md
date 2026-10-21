<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-inverse.svg">
    <img src="docs/assets/logo.svg" alt="Veyra" height="72">
  </picture>
</p>

<p align="center"><b>Etiqueta, entrena y evalúa detectores de objetos sobre imágenes y video, en equipo.</b></p>

<p align="center">
  <a href="LICENSE"><img alt="Licencia: AGPL-3.0" src="https://img.shields.io/badge/licencia-AGPL--3.0-78455F"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-2B2029">
  <img alt="PostgreSQL 16" src="https://img.shields.io/badge/PostgreSQL-16-2B2029">
  <img alt="React + TypeScript" src="https://img.shields.io/badge/React-TypeScript-2B2029">
</p>

# Veyra

Plataforma multiusuario, organizada por proyectos, para **etiquetar** imágenes de video con cajas, construir **datasets
versionados**, **entrenar y evaluar** detectores, **combinar** modelos y procedimientos propios en flujos de detección, y verlos
funcionar **en vivo** (video grabado, RTSP o MJPEG). Es agnóstica al dominio: las clases, atajos y colores viven en la
configuración de cada proyecto. Nació para detectar piojos en lana (esa es su prueba de concepto) y se usa igual con cualquier
otro objeto.

> Antes «LabelHub». El nombre de algunas variables (`LABELHUB_*`) y de la base se mantiene por compatibilidad.

**Contenido:** [Qué hace](#qué-hace) · [Arquitectura](#arquitectura) · [Puesta en marcha](#puesta-en-marcha) · [Despliegue](#despliegue) ·
[Documentación](#documentación) · [Pruebas](#pruebas) · [Seguridad](#seguridad-y-límites) · [Licencia](#licencia)

## Qué hace

| Área | Funciones |
|---|---|
| Etiquetado | Editor de cajas (mover, redimensionar, crear, clases, borrar y restaurar), reproductor del video original (±5 s, tiempo exacto, pasos de un fotograma, instantáneas), carrusel, atajos, papelera de capturas inútiles |
| Modalidades de trabajo | **Revisión** (una persona confirma la versión definitiva) o **colaborativa** (todos editan y la última modificación es la final) |
| Equipo | Proyectos, roles (administrador, revisor, etiquetador, lector), cola de trabajo con bloqueo atómico, reparto con doble etiquetado, acuerdo entre personas |
| Archivos fuente | Gestión → Archivos fuente: videos e imágenes sueltas del proyecto. Carga de imágenes con o sin etiquetas (YOLO o COCO, sueltas o en .zip) y de videos, con subidas que siguen en segundo plano |
| Muestreo de videos | **Automático** (un fotograma cada N segundos entre un inicio y un final) o **manual** en una pantalla propia: el video se reproduce, la instantánea queda encima con el video en pausa y se etiqueta ahí mismo |
| Datos | Versiones de dataset inmutables con reparto train/val/test **por video**, exportación YOLO y COCO |
| Modelos | Evaluación (mAP, precisión, recall, F1, conteo por video, errores), entrenamiento YOLO en GPU con cola y registro, preanotación y búsqueda de imágenes inciertas |
| Flujos de detección | Modelos entrenados, detectores y procedimientos del proyecto combinados con **O / Y**; se aplican al etiquetado asistido, a imágenes, a video, a cámaras en vivo y a la evaluación |
| Espacios de trabajo | Un entorno por proyecto con GPU y versionado, al que se entra con VS Code por SSH, para desarrollar los procedimientos de visión que luego usan los flujos |
| En vivo | Sesiones sobre un video o una cámara con modelo o flujo: cajas, latencia, umbral, pausa, grabación de clips, recuento y «guardar fotograma» que vuelve al etiquetado |
| Operación | Uso de disco y cuota, estado del servicio, copia y restauración de un proyecto, copia verificada de la base |

## Arquitectura

| Capa | Tecnología |
|---|---|
| API | Python ≥ 3.10, FastAPI, SQLAlchemy 2, Alembic |
| Base de datos | PostgreSQL 16 (metadatos, etiquetas, usuarios, colas); los archivos pesados quedan en disco o en un NFS |
| Frontend | React + TypeScript + Vite; el editor de cajas es un módulo independiente del framework |
| GPU | Procesos aparte (entrenar, predecir, en vivo): en la misma máquina o en un equipo remoto con el [worker de Veyra](https://github.com/mgueregath/veyra-worker) |

La API puede estar en un servidor y la GPU en otro equipo: el worker pide trabajos por HTTP y ambos comparten una carpeta. La
detección en vivo exige un token por sesión y la API autentica a las personas y reenvía el flujo.

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

## Despliegue

Imagen de la aplicación y `docker-compose.yml` de ejemplo en [deploy/](deploy/README.md) (API + PostgreSQL, datos en un volumen
o NFS). La GPU en otro equipo se conecta con el [worker](https://github.com/mgueregath/veyra-worker); ver [docs/WORKERS.md](docs/WORKERS.md).

## Documentación

| Documento | Contenido |
|---|---|
| [docs/PLAN.md](docs/PLAN.md) | Plan, decisiones y resultados medidos |
| [docs/WORKERS.md](docs/WORKERS.md) | GPU en otro equipo: modos, carpeta compartida y alta de un worker |
| [docs/FLUJOS.md](docs/FLUJOS.md) | Flujos de detección O / Y, procedimientos del espacio de trabajo y uso en vivo |
| [deploy/README.md](deploy/README.md) | Despliegue en un servidor |

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
- Ultralytics (YOLO) es AGPL-3.0, igual que Veyra: ver [Licencia](#licencia).
- El recuento de piojos únicos en vivo es una cota superior (cámara en la mano).

## Licencia

Veyra es software libre bajo la **GNU Affero General Public License v3.0** ([AGPL-3.0](LICENSE)). Si lo modificas y ofreces el
servicio a otras personas por una red, debes ofrecerles el código fuente de tu versión. Es la misma licencia de Ultralytics, el
motor de detección que usa para entrenar y ejecutar modelos YOLO.

Los logotipos y el kit de marca «Ciruela» (`docs/assets/`, `frontend/public/brand/`) se distribuyen con el proyecto; la tipografía
DejaVu conserva su licencia ([FONT-LICENSE.txt](docs/assets/FONT-LICENSE.txt)). No se ha verificado la disponibilidad registral
del nombre.
