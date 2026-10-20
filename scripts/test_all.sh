#!/usr/bin/env bash
# Corre TODAS las pruebas: lint, tipos, backend, núcleo del editor y las cuatro suites en Chrome real.
# Requisitos: la base en marcha (docker compose up -d), `npm run build` en frontend/ y Google Chrome instalado.
set -euo pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate
echo "== lint"; (cd backend && ruff check app tests ../workers ../plugins && ruff format --check app tests ../workers ../plugins)
echo "== frontend"; (cd frontend && npx tsc --noEmit && npx vitest run && npm run build)
echo "== backend"; (cd backend && python -m pytest -q)
for t in test_editor test_workflow_ui test_training_ui test_live_ui; do echo "== chrome: $t"; python3 -u tests/ui/$t.py | tail -3; done
echo "TODO OK"
