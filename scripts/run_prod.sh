#!/usr/bin/env bash
# Arranca Veyra con la configuración de trabajo (datos reales). Uso: scripts/run_prod.sh [direcciones] [puerto]
#   por defecto: 10.0.0.4,100.64.0.4 y el puerto 8899. El registro queda en data/server.log.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
export LABELHUB_MODELS_ROOT="$ROOT/data/models" LABELHUB_EXPORT_ROOT="$ROOT/data/exports" LABELHUB_RUNS_ROOT="$ROOT/data/runs"
export LABELHUB_MEDIA_ROOT="${LABELHUB_MEDIA_ROOT:-$ROOT/../cv-residue-diff/labeling}"
mkdir -p data
exec "$ROOT/.venv/bin/python" scripts/serve.py --host "${1:-10.0.0.4,100.64.0.4}" --port "${2:-8899}" >> data/server.log 2>&1
