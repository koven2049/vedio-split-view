#!/bin/bash
# LaunchDaemon com.koven.vedio-split-view 入口。
# 等 podman machine 就绪后再 manage.sh start（开机时 daemon 可能早于 VM）。
# 不在这里等公网 DNS：运行时解析走 aardvark → pasta/宿主机，与启动竞态无关。
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$DIR/logs"

for _ in $(seq 1 60); do
    if podman info >/dev/null 2>&1; then
        break
    fi
    sleep 5
done

if ! podman info >/dev/null 2>&1; then
    echo "$(date '+%F %T') podman machine 未就绪, 放弃" >&2
    exit 1
fi

cd "$DIR" || exit 1
exec ./manage.sh start
