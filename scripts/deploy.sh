#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
printf '%s\n' '此版本提供本地运行与源码打包，不包含公网部署。' '运行：sh scripts/start.sh' '交付打包：sh scripts/build.sh' '真实档案部署前置清单：docs/deployment.md'
