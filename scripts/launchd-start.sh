#!/bin/bash
# LaunchDaemon com.koven.vedio-split-view 常驻巡检。
# 根因：Podman VM 崩溃后容器是 Exited(0)，unless-stopped 不拉起；
# 本脚本若 start 完就退出，launchd（KeepAlive=false）也不会再跑。
# 不 hard-restart machine（交给 com.koven.multica，避免两家互踩）。
# 日志: logs/launchd.log（只记失败与拉起）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

DIR="$(cd "$(dirname "$0")/.." && pwd)"
INTERVAL=30
mkdir -p "$DIR/logs"

log() { echo "$(date '+%F %T') $*"; }

LOCKDIR="$DIR/logs/watchdog.lockdir"
acquire_lock() {
    while ! mkdir "$LOCKDIR" 2>/dev/null; do
        local oldpid
        oldpid="$(cat "$LOCKDIR/pid" 2>/dev/null || true)"
        if [[ -n "$oldpid" ]] && kill -0 "$oldpid" 2>/dev/null; then
            log "已有 watchdog pid=$oldpid, 等待接手"
            sleep 5
            continue
        fi
        rm -rf "$LOCKDIR"
    done
    echo $$ > "$LOCKDIR/pid"
    trap 'rm -rf "$LOCKDIR"' EXIT
}
acquire_lock

podman_ok() {
    podman info >/dev/null 2>&1
}

ensure_machine() {
    if podman_ok; then
        return 0
    fi
    log "podman 未就绪, 尝试 machine start"
    podman machine start >/dev/null 2>&1 || true
    local i
    for i in $(seq 1 36); do
        if podman_ok; then
            return 0
        fi
        sleep 5
    done
    log "podman machine 仍未就绪"
    return 1
}

health_ok() {
    curl -sf --noproxy '*' --max-time 3 "http://127.0.0.1:8080/health" >/dev/null 2>&1 \
        && curl -sf --noproxy '*' --max-time 3 "http://127.0.0.1:5180/" >/dev/null 2>&1
}

bring_up() {
    cd "$DIR" || return 1
    ensure_machine || return 1
    ./manage.sh start
    local i
    for i in $(seq 1 30); do
        if health_ok; then
            return 0
        fi
        sleep 2
    done
    return 1
}

log "watchdog 启动"
if bring_up; then
    log "栈已就绪"
else
    log "首次拉起未成功, 进入巡检重试"
fi

fails=0
while true; do
    if health_ok; then
        fails=0
    else
        fails=$((fails + 1))
        log "health 失败 (#${fails}), 拉起栈"
        bring_up || true
    fi
    sleep "$INTERVAL"
done
