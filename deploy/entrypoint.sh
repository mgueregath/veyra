#!/bin/sh
# Aplica las migraciones pendientes y arranca la API.
set -e
cd /app/backend
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${VEYRA_PORT:-8899}" --proxy-headers
