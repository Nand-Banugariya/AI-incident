#!/bin/sh
# Runs sql/schema.sql with the seed size from DEMO_ORDERS_ROWS (the entrypoint only runs top-level files).
set -e
psql -v ON_ERROR_STOP=1 -v rows="${DEMO_ORDERS_ROWS:-2000000}" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -f /docker-entrypoint-initdb.d/sql/schema.sql
