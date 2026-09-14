#!/bin/bash
# LaunchDaemon com.koven.vedio-split-view 常驻巡检。
# 根因：Podman VM 崩溃后容器 Exited(0)，unless-stopped 不拉起。
# 误杀：单次 3s health 超时就 manage.sh start（删容器），会把正在分析的任务打断。
# 规则：容器没在跑 → 立刻拉；容器在跑但 health 失败 → 连续 NEED 次才重建。
# 不 hard-restart machine（交给 com.koven.multica）。
# 日志: logs/launchd.log（只记失败与拉起）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

DIR="$(cd "$(dirname "$0")/.." && pwd)"
INTERVAL=30
NEED=4
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
        if health_ok; then
            return 0
        fi
        sleep 2
    done
    return 1
}

log "watchdog 启动"
if health_ok && containers_up; then
    log "栈已就绪"
else
    if bring_up; then
        log "栈已就绪"
    else
        log "首次拉起未成功, 进入巡检重试"
    fi
fi

fails=0
while true; do
    if health_ok && containers_up; then
        fails=0
    elif ! containers_up; then
        fails=0
        log "容器未在跑, 拉起栈"
        bring_up || true
    else
        fails=$((fails + 1))
        if [[ "$fails" -ge "$NEED" ]]; then
            log "health 连续失败 (#${fails}/${NEED}), 重建容器"
            fails=0
            bring_up || true
        else
            log "health 失败 (#${fails}/${NEED}), 容器仍在跑, 再等"
        fi
    fi
    sleep "$INTERVAL"
done
