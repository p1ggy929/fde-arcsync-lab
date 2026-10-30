#!/bin/sh
# 仅部署/回切已构建的不可变镜像标签；不自动恢复或覆盖数据库。
set -eu
cd "$(dirname "$0")/.."
: "${1:?请提供已构建并完成兼容性验证的镜像标签}"
case "$1" in *:latest|*[!a-zA-Z0-9._:/@-]*) echo '请使用明确版本标签或镜像摘要'; exit 1;; esac
case "$1" in *:*) ;; *) echo '镜像必须含明确版本标签或摘要'; exit 1;; esac
export ARCSYNC_IMAGE="$1"
docker compose --env-file .env.production -f config/production/compose.yml config --quiet
docker compose --env-file .env.production -f config/production/compose.yml up -d --no-deps app
attempt=0
while [ "$attempt" -lt 30 ]; do
  if docker compose --env-file .env.production -f config/production/compose.yml exec -T app python -c 'import os,urllib.request;urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8765/api/ready",headers={"Host":os.environ["ARCSYNC_ALLOWED_HOSTS"].split(",")[0],"X-Forwarded-Proto":"https"}),timeout=5)' >/dev/null 2>&1; then
    echo '依赖就绪检查通过；请继续验收域名HTTPS、登录、权限、检索和人工流程。'; exit 0
  fi
  attempt=$((attempt+1)); sleep 2
done
echo '就绪检查失败。未自动覆盖数据；请检查日志或用上一兼容镜像标签执行本脚本回切。'; exit 1
