#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
    echo '请先复制 .env.example 为 .env，并填写两个 MySQL 密码。' >&2
    exit 1
fi
exec docker compose --env-file .env -f config/docker-compose.mysql.yml up -d
