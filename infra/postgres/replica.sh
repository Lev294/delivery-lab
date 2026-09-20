#!/bin/bash
set -euo pipefail
if [ "$(id -u)" = 0 ]; then
  mkdir -p "$PGDATA"
  chown postgres:postgres "$PGDATA"
  chmod 700 "$PGDATA"
  exec gosu postgres bash /opt/replica.sh
fi
umask 077
printf 'postgres:5432:replication:replicator:%s\n' "$REPLICATION_PASSWORD" > /tmp/replication.pgpass
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  pg_basebackup -d 'host=postgres port=5432 user=replicator passfile=/tmp/replication.pgpass' \
    -D "$PGDATA" -R -X stream --checkpoint=fast --no-password
fi
test -f "$PGDATA/standby.signal"
exec postgres -c hot_standby=on -c shared_buffers=64MB -c max_connections=60
