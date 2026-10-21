# Despliegue de Veyra

Servidor de la aplicación: `/home/docker/veyra` (compose + `.env`). Imágenes en `docker.labs.hapticus.cl`.

```bash
cd /home/docker/veyra
docker compose pull && docker compose up -d      # actualizar a VEYRA_VERSION
docker compose logs -f api
docker exec veyra-db pg_dump -U labelhub labelhub | gzip > backup_$(date +%F).sql.gz     # copia de la base
```

- **Datos:** el volumen `veyra_data` es el NFS (`/mnt/main/veyra`): `media/` (imágenes), `videos/` (originales), `models/`, `runs/`, `exports/`.
  Las imágenes y videos se referencian por ruta: dentro del contenedor viven en `/data/...`, y el worker de GPU monta lo mismo en `/data`.
- **GPU:** `VEYRA_GPU_MODE=remote` ⇒ entrenar, detectar imágenes y el modo en vivo requieren un worker registrado (Gestión → Sistema).
- **Base de datos:** PostgreSQL en `./pgdata` (disco local del servidor, no en el NFS).
