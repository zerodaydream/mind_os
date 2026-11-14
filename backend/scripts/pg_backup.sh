#!/usr/bin/env sh
# Local backup: pg_dump mindos → ./data/backups/mindos_YYYYMMDD.sql
set -e
mkdir -p data/backups
docker compose exec -T postgres pg_dump -U mindos mindos > "data/backups/mindos_$(date +%Y%m%d).sql"
