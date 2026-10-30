#!/bin/sh
# macOS: double-click this file in Finder.
cd "$(dirname "$0")" || exit 1
sh scripts/start.sh "$@"
launch_status=$?
if [ "$launch_status" -ne 0 ] && [ -t 0 ]; then
    printf '\n启动未完成，请查看上方提示。按回车关闭…'
    read -r answer
fi
exit "$launch_status"
