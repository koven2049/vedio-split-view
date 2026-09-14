#!/bin/bash
# LaunchDaemon com.koven.vedio-split-view 常驻巡检。
# VM 崩溃后容器 Exited(0)，unless-stopped 不拉 → 只在「容器没在跑」时 manage.sh start。
# 禁止用 HTTP /health 超时当死亡信号：分析一忙事件循环卡住，health 失败不等于挂了，
# start 会删容器，把正在跑的任务打死。
# 不 hard-restart machine（交给 com.koven.multica）。
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

DIR="$(cd "$(dirname "$0")/.." && pwd)"
INTERVAL=30
BACKEND=vsplit-backend
FRONTEND=vsplit-frontend
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

container_running() {
    [[ "$(podman inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

containers_up() {
    container_running "$BACKEND" && container_running "$FRONTEND"
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
    curl -sf --noproxy '*' --max-time 8 "http://127.0.0.1:8080/health" >/dev/null 2>&1
}

bring_up() {
    cd "$DIR" || return 1
    ensure_machine || return 1
    ./manage.sh start
    local i
    for i in $(seq 1 30); do
        if containers_up; then
            return 0
        fi
        sleep 2
    done
    return 1
}

log "watchdog 启动"
if containers_up; then
    log "栈已就绪"
else
    if bring_up; then
        log "栈已就绪"
    else
        log "首次拉起未成功, 进入巡检重试"
    fi
fi

health_fails=0
while true; do
    if containers_up; then
        if health_ok; then
            health_fails=0
        else
            health_fails=$((health_fails + 1))
            log "health 失败 (#${health_fails}), 容器仍在跑, 不重建"
        fi
    else
        health_fails=0
        log "容器未在跑, 拉起栈"
        bring_up || true
    fi
    sleep "$INTERVAL"
done
