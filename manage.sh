#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

_deploy_check_sh="$(dirname "${BASH_SOURCE[0]}")/../coding/deploy-common/deploy-check.sh"
if [[ -f "$_deploy_check_sh" ]]; then
    # shellcheck source=/dev/null
    source "$_deploy_check_sh"
else
    # Fallback: remote machines lack scripts/ from parent repo
    _check_deploy_target() { :; }
fi

# ── colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }

# ── constants ─────────────────────────────────────────────────────────────────
DATA_DIR="./data"
CONFIG_DIR="./config"
CONFIG_FILE="$CONFIG_DIR/app.yaml"
CONFIG_EXAMPLE="$CONFIG_DIR/app.yaml.example"
DEPLOY_CONFIG_FILE="$CONFIG_DIR/deploy.cfg"
DEPLOY_CONFIG_EXAMPLE="$CONFIG_DIR/deploy.cfg.example"
BUILD_CONFIG_FILE="$CONFIG_DIR/build.cfg"
BUILD_CONFIG_EXAMPLE="$CONFIG_DIR/build.cfg.example"
PODMAN_CMD="${PODMAN_CMD:-podman}"

DEPLOY_EXCLUDE_FILE=".rsync-exclude"
BACKEND_CONTAINER="vsplit-backend"

# ── process-mode constants ────────────────────────────────────────────────────
VENV_PY="$SCRIPT_DIR/backend/.venv/bin/python"
DIST_DIR="$SCRIPT_DIR/frontend/dist"
LAUNCHD_LABEL="${LAUNCHD_LABEL:-com.koven.vsplit.web}"
PIDFILE="$SCRIPT_DIR/logs/multi_listen.pid"
# 双形态（2026-11-13 规约）：生产（mini）= 系统 LaunchDaemon（UserName=admin +
# KeepAlive，restart 免 sudo=kill 后 KeepAlive 拉起，真正停用走免密 svc-ctl
# bootout）；Test（开发检出）= pidfile + nohup，可随时中断，绝不托管给系统
# ——install-daemon 有 Test 防呆（无部署章即拒绝）。
# 实例身份 = 部署盖章：deploy/deploy-local 成功后在目标副本写
# config/env=production（唯一写入点）；两机开发检出永远无章 = Test。

# ── 实例身份 = 部署盖章 ──────────────────────────────────────────────────────
prod_stamp_file() { printf '%s/config/env' "$SCRIPT_DIR"; }
is_prod_instance() { [[ "$(cat "$(prod_stamp_file)" 2>/dev/null || true)" == production ]]; }
refuse_if_prod_source() {
    is_prod_instance || return 1
    log_error "本目录是生产副本（config/env=production，由 deploy 盖章）——不能作为部署源。"
    log_error "部署请在开发检出执行（本机或 mini 的 ~/Documents/... 检出，均无章=Test）。"
    return 0
}
system_managed() {
    command -v launchctl &>/dev/null || return 1
    launchctl print "system/$LAUNCHD_LABEL" &>/dev/null
}

# ── 进程侦查助手 ─────────────────────────────────────────────────────────────
# 监听 SPA 端口的 PID（无监听/无 lsof → 空）。launchd 直接拉起的实例没有
# pidfile，只能靠端口判断。lsof 无匹配时退出码为 1，须归零以免 set -e 误杀。
port_listener_pids() {
    command -v lsof &>/dev/null || return 0
    lsof -nP -iTCP:"$(get_frontend_port)" -sTCP:LISTEN -t 2>/dev/null || true
}

# 只保留确认是本目录实例的监听 PID：命令行含本目录的 multi_listen.py。
# 端口可能被无关进程占用——身份不确认绝不杀（launchd 与 nohup 实例都带该路径）。
confirmed_server_pids() {
    local pid cmdline
    while IFS= read -r pid; do
        if [[ -n "$pid" ]]; then
            cmdline="$(ps -p "$pid" -o command= 2>/dev/null || true)"
            if [[ "${cmdline:-}" == *"$SCRIPT_DIR/scripts/multi_listen.py"* ]]; then
                printf '%s\n' "$pid"
            fi
        fi
    done < <(port_listener_pids)
    return 0
}

alive() { kill -0 "$1" 2>/dev/null; }

# 停一个 PID：先 TERM，等 ${STOP_TERM_WAIT:-3}s 不退再 KILL，再等
# ${STOP_KILL_WAIT:-2}s。仍活着（杀不动/权限不够）返回 1。
stop_pid() {
    local pid="$1" waits="${STOP_TERM_WAIT:-3}" i
    kill -TERM "$pid" 2>/dev/null || true
    for ((i = 0; i < waits * 10; i++)); do alive "$pid" || return 0; sleep 0.1; done
    log_warn "pid $pid ignored SIGTERM — sending SIGKILL …"
    kill -KILL "$pid" 2>/dev/null || true
    waits="${STOP_KILL_WAIT:-2}"
    for ((i = 0; i < waits * 10; i++)); do alive "$pid" || return 0; sleep 0.1; done
    alive "$pid" && return 1
    return 0
}

# 端口被"别的目录"的本服务实例占用（典型：同机生产副本占着端口）。
port_held_elsewhere() {
    local listener
    listener="$(port_listener_pids | head -n 1)"
    [[ -n "$listener" ]] || return 1
    [[ -z "$(confirmed_server_pids | head -n 1)" ]]
}

# launchd 托管兜底判断（restart 观察窗用；托管态判定以目录级
# daemon_managed_here 为准）。rc=0 托管；1 未托管；2 查不了（无 launchctl）。
launchd_managed() {
    command -v launchctl &>/dev/null || return 2
    launchctl print "system/$LAUNCHD_LABEL" &>/dev/null && return 0
    launchctl print "gui/$(id -u)/$LAUNCHD_LABEL" &>/dev/null && return 0
    return 1
}

# 目录级托管判定（2026-11-13 修订）：同机 Test 检出（~/Documents/...）与生产
# 副本（~/Services/...）共用同一个 label——只看 label 会把 Test 的 start/stop
# 误判成"生产托管中"。必须：label 在 system 域 **且** 监听进程属本目录。
daemon_managed_here() {
    system_managed || return 1
    [[ -n "$(confirmed_server_pids | head -n 1)" ]]
}

# 免密服务控制：root 所有的 svc-ctl 包装（白名单 label，只开放
# bootout/bootstrap/kickstart 三个动作）。缺失时相关操作降级为指引，不硬错。
SVC_CTL=/usr/local/bin/svc-ctl
svc_ctl() { sudo -n "$SVC_CTL" "$@"; }

# 系统域托管守卫（仅当本目录实例被 Daemon 托管时触发）：软停无意义——kill 后
# KeepAlive 立即拉起。真停 = svc_ctl bootout；helper 未装时降级为准确指引。
# 返回码：0=已真停完成；1=非本目录托管（调用方走各自路径）；2=托管但 helper
# 未装、无法免密真停（调用方必须拒绝软停，exit 1）。
guard_prod_daemon_stop() {
    daemon_managed_here || return 1
    if svc_ctl bootout "system/$LAUNCHD_LABEL" 2>/dev/null; then
        sleep 1
        local pid; pid="$(confirmed_server_pids | head -n 1)"
        [[ -n "$pid" ]] && stop_pid "$pid" || true
        log_ok "已停用（svc_ctl bootout）。重新上线：./manage.sh start（走 bootstrap）。"
        return 0
    fi
    log_warn "本目录实例由系统 LaunchDaemon 托管（KeepAlive），软停会被立即拉起。"
    log_warn "免密真停需一次性安装 svc-ctl helper（root）："
    log_warn "  sudo bash ~/Services/install-svc-ctl.sh"
    log_warn "装好后本命令即可真停；或手动 ! sudo launchctl bootout system/$LAUNCHD_LABEL"
    return 2
}

# True only when EVERY listener answers /health. multi_listen.py binds
# :5180 (SPA+API) and :8080 (API compat) in one process; one port up without
# the other is a broken start, not a healthy one (2026-11-01 T54 acceptance).
# /health 与 api 前缀下的健康别名是同一路由（backend main.py 同时注册两个）。
_ports_healthy() {
    local port
    for port in "$(get_frontend_port)" "$(get_app_port)"; do
        curl -sf --noproxy '*' "http://localhost:${port}/health" >/dev/null 2>&1 || return 1
    done
    return 0
}

# 轮询双端口健康，超时 ${HEALTH_WAIT:-30}s 返回 1。
wait_health() {
    local timeout="${HEALTH_WAIT:-30}" i
    for ((i = 0; i < timeout; i++)); do _ports_healthy && return 0; sleep 1; done
    return 1
}

# ── config helpers ────────────────────────────────────────────────────────────
read_yaml_value() {
    local key="$1" default="$2"
    if [[ ! -f "$CONFIG_FILE" ]]; then echo "$default"; return; fi
    local section="${key%%.*}" field="${key#*.}"
    local in_section=0 val=""
    while IFS= read -r line; do
        if echo "$line" | grep -qE "^${section}:"; then in_section=1; continue; fi
        if [[ $in_section -eq 1 ]] && echo "$line" | grep -qE "^[a-zA-Z]"; then break; fi
        if [[ $in_section -eq 1 ]] && echo "$line" | grep -qE "^[[:space:]]+${field}:"; then
            val=$(echo "$line" | sed "s/^[^:]*:[[:space:]]*//" | sed 's/[[:space:]]*#.*//' | sed 's/^"//' | sed 's/"$//')
            break
        fi
    done < "$CONFIG_FILE"
    echo "${val:-$default}"
}

get_app_port()      { read_yaml_value "app.port" "8080"; }
get_frontend_port() { read_yaml_value "app.frontend_port" "5180"; }
get_admin_password() { read_yaml_value "admin.password" ""; }

ensure_config() {
    [[ -f "$CONFIG_FILE" ]] || { log_error "Config not found. Run: $0 init"; exit 1; }
}

_admin_login() {
    local port="$1" admin_user="${2:-admin}" admin_pass="$3"
    curl -sf --noproxy '*' "http://localhost:$port/api/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$admin_user\",\"password\":\"$admin_pass\"}" \
        | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4
}

# ── data-health helpers ───────────────────────────────────────────────────────
# The production DB is a host-side SQLite file bind-mounted read-write into the
# backend container ($SCRIPT_DIR/data:/app/data). A vanished bind-mount target
# once let a container write to an orphan inode for two months; a prune then made
# the data physically disappear. These helpers make the DB's location and row
# counts VISIBLE so drift/loss is caught by eye instead of silently.

# Absolute path of the DB, resolved from $SCRIPT_DIR so it can never be a
# drifting relative/external path.
_db_abs_path() { echo "$SCRIPT_DIR/data/video_split.db"; }

# Row count for a table. Prefers host sqlite3; falls back to the backend
# container's bundled python (sqlite3 is stdlib) so row counts stay visible
# even when the host lacks sqlite3. Prints a plain integer, or "n/a" when the
# DB is absent or every path fails. Never aborts under `set -e`.
_db_count() {
    local table="$1" db n
    db="$(_db_abs_path)"
    [[ -f "$db" ]] || { echo "n/a"; return 0; }

    if command -v sqlite3 >/dev/null 2>&1; then
        n="$(sqlite3 "$db" "SELECT COUNT(*) FROM $table;" 2>/dev/null || true)"
        [[ "$n" =~ ^[0-9]+$ ]] && { echo "$n"; return 0; }
    fi

    # Fallback: query inside the running backend container (DB at /app/data).
    if command -v "$PODMAN_CMD" >/dev/null 2>&1 \
        && "$PODMAN_CMD" container exists "$BACKEND_CONTAINER" 2>/dev/null; then
        n="$("$PODMAN_CMD" exec "$BACKEND_CONTAINER" python -c \
            "import sqlite3;print(sqlite3.connect('/app/data/video_split.db').execute('SELECT COUNT(*) FROM ${table}').fetchone()[0])" \
            2>/dev/null || true)"
        [[ "$n" =~ ^[0-9]+$ ]] && { echo "$n"; return 0; }
    fi

    echo "n/a"
}

# Print DB location, size, mtime and users/videos/tasks row counts.
# fail-loud: WARN when the DB is missing or users==0 (likely data-source drift/loss).
# Returns 1 on data red (DB missing / users=0); "n/a" row counts stay tolerated (warn).
_report_data_health() {
    local db users videos tasks fail=0
    db="$(_db_abs_path)"
    log_step "Data health:"
    echo "  DB path: $db"

    if [[ ! -f "$db" ]]; then
        log_error "DB file does NOT exist — data source may have drifted or been lost!"
        log_warn  "Expected bind-mount target: $SCRIPT_DIR/data (project-local)."
        return 1
    fi

    # size + mtime (portable-ish: try GNU stat, then BSD stat, then ls fallback)
    local size mtime
    size="$(stat -c %s "$db" 2>/dev/null || stat -f %z "$db" 2>/dev/null || echo '?')"
    mtime="$(stat -c %y "$db" 2>/dev/null || stat -f '%Sm' "$db" 2>/dev/null || echo '?')"
    echo "  Size:    ${size} bytes"
    echo "  Mtime:   ${mtime}"

    users="$(_db_count users)"
    videos="$(_db_count videos)"
    tasks="$(_db_count tasks)"
    echo "  Rows:    users=$users  videos=$videos  tasks=$tasks"

    if [[ "$users" == "0" ]]; then
        log_error "users=0 — DB is EMPTY. Possible data loss or wrong data directory!"
        fail=1
    elif [[ "$users" == "n/a" ]]; then
        log_warn "Could not read row counts (no host sqlite3 and backend container not running). Inspect $db manually."
    else
        log_ok "Data present: $users user(s)."
    fi
    return "$fail"
}

# Post-rebuild smoke check: wait for /health, log in as admin, print row counts.
# fail-loud on empty data (WARN) but NON-blocking — a rebuild that lost data
# should still finish so the operator can react, not hang.
_smoke_check() {
    local port="$1" admin_pass="$2"
    log_step "Smoke check:"

    local ok=0 i
    for i in $(seq 1 30); do
        if curl -sf --noproxy '*' "http://localhost:$port/health" >/dev/null 2>&1; then
            ok=1; break
        fi
        sleep 1
    done
    if [[ "$ok" -ne 1 ]]; then
        log_error "Backend /health did not come up within 30s."
        return 0
    fi
    log_ok "Backend reachable at http://localhost:$port"

    if [[ -n "$admin_pass" ]]; then
        local token
        token="$(_admin_login "$port" admin "$admin_pass" 2>/dev/null || true)"
        if [[ -n "$token" ]]; then
            log_ok "Admin login OK."
        else
            log_warn "Admin login failed (check admin.password in $CONFIG_FILE)."
        fi
    else
        log_warn "admin.password empty in $CONFIG_FILE — skipping login check."
    fi

    # NON-blocking by contract: data red must not fail rebuild (see docstring above).
    _report_data_health || true
}

# ── deploy exclude file ──────────────────────────────────────────────────────
_ensure_deploy_exclude() {
    if [[ ! -f "$DEPLOY_EXCLUDE_FILE" ]]; then
        cat > "$DEPLOY_EXCLUDE_FILE" <<'EXCLUDE'
# rsync exclude rules for code deployment — edit freely
# one rule per line, # = comment
# reference: https://ss64.com/bash/rsync.html

# ── version control & IDE ────────────────────────────────────────────────────
.git/
.DS_Store
.vscode/
.idea/
.claude/
.superpowers/
.code-review-graph/

# ── python cache / build ─────────────────────────────────────────────────────
backend/.venv/
__pycache__/
*.pyc
*.pyo
*.egg-info/
.pytest_cache/

# ── frontend cache / build ───────────────────────────────────────────────────
frontend/node_modules
frontend/node_modules/
frontend/dist/

# ── test fixtures (large audio / video) ──────────────────────────────────────
test/
test_transcribe.py

# ── CI / tooling artefacts ───────────────────────────────────────────────────
.playwright-cli/
.mirror_state

# ── sensitive config (never overwrite remote secrets) ────────────────────────
# exclude all config except examples
config/app.yaml
config/deploy.cfg
config/certs/
config/*_cookies.txt
config/feishu.yaml
config/*.pem
config/*.key

# ── runtime data & logs (never overwrite remote DB / logs) ───────────────────
data/
logs/

# ── this file itself (remote may have its own) ──────────────────────────────
.rsync-exclude
EXCLUDE
        log_info "Created $DEPLOY_EXCLUDE_FILE (edit to customise)"
    fi
}

_ensure_deploy_config() {
    mkdir -p "$CONFIG_DIR"
    if [[ ! -f "$DEPLOY_CONFIG_FILE" ]]; then
        cp "$DEPLOY_CONFIG_EXAMPLE" "$DEPLOY_CONFIG_FILE"
        log_info "Created $DEPLOY_CONFIG_FILE (edit ssh_target / remote_path before deploying)"
    fi
}

_ensure_build_config() {
    mkdir -p "$CONFIG_DIR"
    if [[ ! -f "$BUILD_CONFIG_FILE" ]]; then
        cp "$BUILD_CONFIG_EXAMPLE" "$BUILD_CONFIG_FILE"
        log_info "Created $BUILD_CONFIG_FILE (edit registry / mirrors for your network)"
    fi
}

# ── commands ──────────────────────────────────────────────────────────────────

run_init() {
    log_step "Initialising project …"
    mkdir -p "$CONFIG_DIR" "$DATA_DIR/tmp" logs/nginx

    if [[ ! -f "$CONFIG_FILE" ]]; then
        cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
        log_info "Created $CONFIG_FILE from example — please edit before starting."
    else
        log_info "$CONFIG_FILE already exists"
    fi

    _ensure_deploy_exclude
    _ensure_deploy_config
    _ensure_build_config
    log_ok "Init complete."
}

_ensure_venv() {
    if [[ ! -x "$VENV_PY" ]]; then
        log_step "Creating backend venv (first run) …"
        python3 -m venv "$SCRIPT_DIR/backend/.venv" || { log_error "venv create failed"; exit 1; }
        "$VENV_PY" -m pip install --quiet --upgrade pip
        "$VENV_PY" -m pip install --quiet -e "$SCRIPT_DIR/backend" || { log_error "pip install failed"; exit 1; }
    fi
}

_ensure_dist() {
    [[ -f "$DIST_DIR/index.html" ]] || {
        log_error "frontend/dist missing — run: $0 rebuild"
        exit 1
    }
}

# PID launchd reports for a loaded target, or empty.
# PID launchd reports for a loaded target, or empty.
service_pid() { confirmed_server_pids | head -n 1; }

run_start() {
    if daemon_managed_here; then
        # 本目录实例由 KeepAlive 保活，start 幂等。
        log_ok "Managed by system LaunchDaemon ($LAUNCHD_LABEL) — instance kept alive; start is a no-op."
        return 0
    fi
    if port_held_elsewhere; then
        # 同机另一副本（~/Services 生产实例）占着端口：Test 与生产同端口不能并存。
        log_error "端口 $(get_frontend_port) 已被本服务的生产实例占用（~/Services 副本，系统 Daemon 托管）。"
        log_error "Test 与生产同端口不能并存：要跑本目录实例，先停生产（去 ~/Services 副本执行 stop 会给出 sudo 命令）。"
        exit 1
    fi
    # 有章 = 生产副本（此前被 bootout 停用）：走 bootstrap 回托管，而非 nohup。
    if is_prod_instance; then
        if svc_ctl bootstrap "system/$LAUNCHD_LABEL" 2>/dev/null; then
            sleep 2
            if wait_health; then
                log_ok "Started via svc_ctl bootstrap.  pid $(port_listener_pids | head -n 1)  →  http://localhost:$(get_frontend_port)"
                return 0
            fi
            log_error "Bootstrap done but /health not passing on both ports."
            return 1
        fi
        log_error "免密启动失败：svc-ctl helper 未安装（root 一次性安装）："
        log_error "  sudo bash ~/Services/install-svc-ctl.sh"
        log_error "或手动 ! sudo launchctl bootstrap system /Library/LaunchDaemons/$LAUNCHD_LABEL.plist"
        exit 1
    fi
    # Test（无章）：本目录 nohup 起服务（独立进程，可随时中断，绝不托管）。
    ensure_config
    _ensure_venv
    _ensure_dist
    mkdir -p "$SCRIPT_DIR/logs"
    if [[ -n "$(service_pid)" ]]; then
        log_ok "Already running (pid $(service_pid)) — idempotent, exit 0"
        return 0
    fi
    rm -f "$PIDFILE"
    log_step "Starting $SERVICE (nohup) on :$(get_frontend_port) + :$(get_app_port) …"
    nohup "$VENV_PY" "$SCRIPT_DIR/scripts/multi_listen.py" >>"$SCRIPT_DIR/logs/process.log" 2>&1 &
    echo $! >"$PIDFILE"
    if wait_health && [[ -n "$(service_pid)" ]]; then
        log_ok "Started.  pid $(service_pid)  →  Web http://localhost:$(get_frontend_port)  (API compat: :$(get_app_port))"
        return 0
    fi
    log_error "Failed to start — check logs/process.log"
    local pid; pid="$(service_pid)"
    [[ -n "$pid" ]] && stop_pid "$pid" || true
    rm -f "$PIDFILE"
    return 1
}

run_stop() {
    guard_prod_daemon_stop; local grc=$?
    [[ "$grc" -eq 0 ]] && return 0   # guard 已真停
    [[ "$grc" -eq 2 ]] && exit 1     # 托管但 helper 缺失：拒绝假成功（面板如实显示失败）
    if port_held_elsewhere; then
        log_error "端口上的实例属于生产副本（~/Services，系统 Daemon 托管），不属于本目录——本目录无可停。"
        log_error "要停生产：去 ~/Services 副本执行 ./manage.sh stop（同样会给 sudo 命令），或直接 sudo bootout。"
        exit 1
    fi
    # 停止目标 = pidfile 里的 PID + 监听端口的本服务 multi_listen.py 进程；
    # 身份不确认的进程绝不杀，防误杀同端口的无关服务。
    local targets=() pid t dup cmdline
    if [[ -f "$PIDFILE" ]]; then
        pid="$(cat "$PIDFILE" 2>/dev/null || true)"
        if [[ -n "${pid:-}" ]] && alive "$pid"; then
            cmdline="$(ps -p "$pid" -o command= 2>/dev/null || true)"
            if [[ "${cmdline:-}" == *"$SCRIPT_DIR/scripts/multi_listen.py"* ]]; then
                targets+=("$pid")
            else
                log_warn "pidfile pid $pid is not multi_listen.py (stale pidfile?) — not killing it."
            fi
        else
            log_warn "Process not running."
        fi
    fi
    while IFS= read -r pid; do
        if [[ -n "$pid" ]]; then
            dup=0
            if [[ ${#targets[@]} -gt 0 ]]; then
                for t in "${targets[@]}"; do
                    if [[ "$t" == "$pid" ]]; then dup=1; break; fi
                done
            fi
            if [[ "$dup" != 1 ]]; then targets+=("$pid"); fi
        fi
    done < <(confirmed_server_pids)

    if [[ ${#targets[@]} -eq 0 ]]; then
        log_warn "No pidfile and no multi_listen.py on port $(get_frontend_port) — nothing to stop."
        rm -f "$PIDFILE"
        return 0
    fi

    log_step "Stopping $SERVICE (pid ${targets[*]}) …"
    local failed=0
    for pid in "${targets[@]}"; do
        stop_pid "$pid" || { log_error "Could not stop pid $pid."; failed=1; }
    done
    rm -f "$PIDFILE"
    if [[ "$failed" == 1 ]]; then return 1; fi
    # KeepAlive=true 的 launchd 会在老实例死掉的瞬间拉起新实例：老 PID 全死即视为
    # 停止成功（托管态由 guard 走 bootout 真停，不走这里）。
    local respawned; respawned="$(service_pid)"
    if [[ -n "${respawned:-}" ]]; then
        log_ok "Old instance stopped (launchd KeepAlive already respawned pid $respawned)."
    else
        log_ok "Stopped."
    fi
    return 0
}

run_restart() {
    log_step "Restarting $SERVICE …"
    # 本目录实例被系统 Daemon 托管：kill 后 KeepAlive 自动拉起新进程——重启免
    # sudo；绝不能 nohup 起第二实例。
    if daemon_managed_here; then
        log_info "system LaunchDaemon owns respawn — killing instance and letting KeepAlive restart it …"
        ensure_config
        _ensure_venv
        _ensure_dist
        local pid killed=0
        while IFS= read -r pid; do
            [[ -n "$pid" ]] || continue
            stop_pid "$pid" && killed=1 || log_warn "could not stop pid $pid"
        done < <(confirmed_server_pids)
        [[ "$killed" == 1 ]] || { log_error "No confirmed instance to restart."; return 1; }
    elif port_held_elsewhere; then
        log_error "端口被生产实例（~/Services 副本）占用——restart 不越目录杀生产进程。"
        log_error "要重启生产：去 ~/Services 副本执行 ./manage.sh restart。"
        exit 1
    else
        # 先记下旧实例，restart 结束时必须换成新进程。
        local old_pids=" " pid
        while IFS= read -r pid; do
            if [[ -n "$pid" ]]; then old_pids+="$pid "; fi
        done < <(confirmed_server_pids)
        run_stop || { log_error "Restart failed: old instance would not stop."; return 1; }

        # Test（非托管）：nohup 拉起本目录实例；查不到 launchctl → 退化为
        # ${LAUNCHD_GRACE:-5}s 观察窗判断（非 mac 宿主）。
        local md=0
        launchd_managed || md=$?
        if [[ "$md" -eq 0 ]]; then
            log_info "launchd ($LAUNCHD_LABEL) KeepAlive owns respawn — waiting for it; never starting a second instance."
        elif [[ "$md" -eq 1 ]]; then
            log_info "Not launchd-managed (label $LAUNCHD_LABEL not loaded) — starting via nohup."
            run_start || { log_error "Restart failed: start failed."; return 1; }
        else
            local grace="${LAUNCHD_GRACE:-5}" i respawned=""
            for ((i = 0; i < grace * 2; i++)); do
                while IFS= read -r pid; do
                    if [[ -n "$pid" ]]; then
                        if [[ "$old_pids" != *" $pid "* ]]; then respawned="$pid"; break 2; fi
                    fi
                done < <(confirmed_server_pids)
                sleep 0.5
            done
            if [[ -n "$respawned" ]]; then
                log_info "Instance respawned externally (launchd KeepAlive, pid $respawned) — not starting a second one."
            else
                run_start || { log_error "Restart failed: start failed."; return 1; }
            fi
        fi
    fi

    # 健康检查不通过就不算重启完成——否则卡死的服务也会被报成「重启完成」。
    log_info "Waiting for health on both ports …"
    if wait_health; then
        log_ok "Restarted.  pid $(service_pid)  →  Web http://localhost:$(get_frontend_port)  (API compat: :$(get_app_port))"
        return 0
    fi
    log_error "Restart failed: /health not passing after ${HEALTH_WAIT:-30}s."
    return 1
}

run_rebuild() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            # kept for muscle-memory compat; process mode has no image cache
            -n|--no-cache|-p|--pull) log_info "$1 accepted (no-op in process mode)" ;;
            *) log_error "Unknown option: $1"; exit 1 ;;
        esac
        shift
    done

    ensure_config

    log_step "Installing backend deps (venv) …"
    _ensure_venv
    "$VENV_PY" -m pip install --quiet -e "$SCRIPT_DIR/backend" \
        || { log_error "backend pip install failed"; exit 1; }
    log_ok "backend"

    log_step "Building frontend …"
    # nvm node lives outside launchd's PATH — source it explicitly.
    if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
        # shellcheck disable=SC1091
        . "$HOME/.nvm/nvm.sh"
    fi
    command -v npm >/dev/null 2>&1 || { log_error "npm not found (install node via nvm)"; exit 1; }
    (cd "$SCRIPT_DIR/frontend" && npm ci --no-audit --no-fund && npm run build) \
        || { log_error "frontend build failed"; exit 1; }
    log_ok "frontend ($(du -sh "$DIST_DIR" 2>/dev/null | cut -f1))"

    log_step "Restarting service …"
    run_restart

    echo
    _smoke_check "$(get_frontend_port)" "$(get_admin_password)"

    log_ok "Done. Web: http://localhost:$(get_frontend_port)"
}

run_status() {
    local fail=0 web_port api_port
    web_port="$(get_frontend_port)"
    api_port="$(get_app_port)"

    log_step "Process status:"
    local pid
    pid="$(service_pid)"
    if [[ -n "$pid" ]]; then
        log_ok "Process: running (pid $pid)"
    else
        log_error "Process: NOT running"
        fail=1
    fi
    # 托管形态只在托管时显示（Test 本目录跑不占行）。
    if system_managed; then log_info "托管: system LaunchDaemon ($LAUNCHD_LABEL)"; fi
    echo

    for port in "$web_port" "$api_port"; do
        if curl -sf --noproxy '*' "http://localhost:${port}/health" >/dev/null 2>&1; then
            log_ok "http://localhost:${port} — health OK"
        else
            log_error "http://localhost:${port} — health FAILED"
            fail=1
        fi
    done

    echo
    _report_data_health || fail=1
    return "$fail"
}

run_backup() {
    ensure_config
    local enabled dir max_copies db_path
    enabled=$(read_yaml_value "backup.enabled" "false")
    dir=$(read_yaml_value "backup.dir" "")
    max_copies=$(read_yaml_value "backup.max_copies" "3")
    db_path=$(read_yaml_value "storage.db_path" "data/video_split.db")

    if [[ "$enabled" != "true" ]]; then
        log_warn "backup.enabled is not true in app.yaml — skipping."
        return 0
    fi
    if [[ -z "$dir" ]]; then
        log_error "backup.dir is empty in app.yaml."
        exit 1
    fi
    if [[ ! -f "$db_path" ]]; then
        log_error "Database not found: $db_path"
        exit 1
    fi

    mkdir -p "$dir"
    local ts filename
    ts=$(date +%Y%m%d_%H%M%S)
    filename="video_split_${ts}.db"
    log_step "Backing up database → $dir/$filename"
    cp "$db_path" "$dir/$filename"
    log_ok "Backup created: $dir/$filename ($(du -h "$dir/$filename" | cut -f1))"

    # Retention: keep newest $max_copies, delete the rest
    local count deleted=0
    count=$(ls -1 "$dir"/video_split_*.db 2>/dev/null | wc -l | tr -d ' ')
    if [[ $count -gt $max_copies ]]; then
        ls -1t "$dir"/video_split_*.db | tail -n +$((max_copies + 1)) | while IFS= read -r old; do
            rm -f "$old"
            deleted=$((deleted + 1))
            log_info "Removed old backup: $(basename "$old")"
        done
    fi
    log_ok "Retention: $count total, keeping $max_copies"
}

run_export() {
    ensure_config
    local port
    port="$(get_app_port)"
    local platform="" admin_user="admin"

    for arg in "$@"; do
        case "$arg" in
            youtube|bilibili) platform="$arg" ;;
            *) admin_user="$arg" ;;
        esac
    done

    if [[ -n "$platform" ]]; then
        log_step "Exporting $platform videos …"
    else
        log_step "Exporting all videos …"
    fi

    local admin_pass
    read -r -s -p "Admin password: " admin_pass; echo

    local token
    token=$(_admin_login "$port" "$admin_user" "$admin_pass")
    [[ -z "$token" ]] && { log_error "Login failed."; exit 1; }

    local url="http://localhost:$port/api/admin/export"
    [[ -n "$platform" ]] && url="${url}?platform=${platform}"

    curl -sf --noproxy '*' -X POST "$url" -H "Authorization: Bearer $token"
    echo
    log_ok "Export complete → data/exports/"
}

run_import() {
    ensure_config
    local port
    port="$(get_app_port)"
    local target_user="${1:-}" admin_user="${2:-admin}" admin_pass="${3:-}"

    if [[ -z "$target_user" ]]; then
        log_error "Usage: $0 import <target_user> [admin_user]"
        echo "  Imports JSON from data/exports/ into the user's library (skip existing)."
        exit 1
    fi

    log_step "Importing into user '$target_user' …"
    if [[ -z "$admin_pass" ]]; then
        read -r -s -p "Admin password: " admin_pass; echo
    fi

    local token
    token=$(_admin_login "$port" "$admin_user" "$admin_pass")
    [[ -z "$token" ]] && { log_error "Login failed."; exit 1; }

    curl -sf --noproxy '*' -X POST "http://localhost:$port/api/admin/import?target_username=$target_user" \
        -H "Authorization: Bearer $token"
    echo
    log_ok "Import complete."
}

run_deploy() {
    local dry_run=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--dry-run) dry_run=true ;;
            *) log_error "Unknown option: $1"; exit 1 ;;
        esac
        shift
    done

    local cfg="${SCRIPT_DIR}/config/deploy.cfg"
    if [[ ! -f "${cfg}" ]]; then
        log_error "config/deploy.cfg 不存在，请从 deploy.cfg.example 复制并配置"
        exit 1
    fi

    local ssh_target remote_path rsync_opts
    ssh_target="$(grep '^ssh_target' "${cfg}" | head -1 | cut -d '=' -f2 | xargs || true)"
    remote_path="$(grep '^remote_path' "${cfg}" | head -1 | cut -d '=' -f2 | xargs || true)"
    rsync_opts="$(grep '^rsync_opts' "${cfg}" | head -1 | cut -d '=' -f2- | xargs || true)"
    local extra_excludes=()
    local raw val
    while IFS= read -r raw; do
        val="${raw#*=}"
        val="${val#"${val%%[![:space:]]*}"}"
        val="${val%"${val##*[![:space:]]}"}"
        [[ -n "$val" ]] && extra_excludes+=("$val")
    done < <(grep -E '^exclude[[:space:]]*=' "${cfg}" || true)

    if [[ -z "${ssh_target}" || -z "${remote_path}" ]]; then
        log_error "deploy.cfg 中 ssh_target 或 remote_path 未配置"
        exit 1
    fi

    _check_deploy_target "${ssh_target}" || { log_error "若确属同机部署（本机开发=本机生产），请用 ./manage.sh deploy-local"; exit 1; }

    # 方向单向（2026-11-13 规约）：生产副本（带部署章）不能作为部署源。
    refuse_if_prod_source && exit 1

    _ensure_deploy_exclude

    local rsync_args=(-avz --progress --delete --exclude-from="$DEPLOY_EXCLUDE_FILE")
    local pat
    for pat in "${extra_excludes[@]+"${extra_excludes[@]}"}"; do
        rsync_args+=(--exclude="$pat")
    done
    if [[ "$dry_run" == true ]]; then
        rsync_args+=(--dry-run)
        log_step "[DRY RUN] Deploying code → ${ssh_target}:${remote_path} …"
    else
        log_step "Deploying code → ${ssh_target}:${remote_path} …"
    fi
    log_info "Exclusion rules: $DEPLOY_EXCLUDE_FILE"
    log_info "Config file: $DEPLOY_CONFIG_FILE"

    if [[ "$dry_run" != true ]]; then
        ssh "$ssh_target" "mkdir -p '$remote_path'" || {
            log_error "SSH 连接失败：无法创建远程目录 ${remote_path}"
            exit 1
        }
    fi

    rsync "${rsync_args[@]}" ${rsync_opts} ./ "${ssh_target}:${remote_path}/" || {
        log_error "rsync 失败：请检查网络连接、SSH 配置或远程路径权限"
        exit 1
    }

    if [[ "$dry_run" == true ]]; then
        echo
        log_ok "Dry run complete — no files were transferred."
    else
        # 部署盖章：目标副本从此是生产实例（唯一写入点；config/*.cfg 被排除不受影响）
        ssh "$ssh_target" "mkdir -p '$remote_path/config' && echo production > '$remote_path/config/env'"
        log_ok "Deploy complete (stamped)."
        echo
        echo "On the remote:"
        echo "  cd ${remote_path} && ./manage.sh rebuild"
    fi
}

run_deploy_local() {
    # Same-machine deploy front door (dev dir = prod dir on this machine):
    # local rsync, no SSH, no deploy-check — that guard intentionally blocks
    # target=self to catch a cross-machine deploy.cfg pointing at itself.
    local dry_run=false target=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--dry-run) dry_run=true ;;
            -*) log_error "Unknown option: $1"; exit 1 ;;
            *) [[ -n "$target" ]] && { log_error "多余的参数: $1"; exit 1; }; target="$1" ;;
        esac
        shift
    done

    target="${target:-$HOME/Services/$(basename "$SCRIPT_DIR")}"
    local excl="$SCRIPT_DIR/.rsync-exclude"
    [[ -f "$excl" ]] || { log_error ".rsync-exclude 不存在 —— 拒绝裸 --delete 部署"; exit 1; }
    [[ -d "$target" ]] || { log_error "目标目录不存在: ${target}（不自动创建，防拼错路径静默部署；确认后 mkdir -p）"; exit 1; }
    [[ "$(cd "$target" && pwd)" == "$SCRIPT_DIR" ]] && { log_error "目标与源目录相同，拒绝执行"; exit 1; }
    # 方向单向（2026-11-13 规约）：生产副本（带部署章）不能作为部署源。
    refuse_if_prod_source && exit 1

    local rsync_args=(-avz --delete --exclude-from="$excl")
    if [[ "$dry_run" == true ]]; then
        rsync_args+=(--dry-run)
        log_step "[DRY RUN] Deploying code (local) → ${target} …"
    else
        log_step "Deploying code (local) → ${target} …"
    fi
    log_info "Exclusion rules: $excl"

    rsync "${rsync_args[@]}" "$SCRIPT_DIR/" "$target/" || {
        log_error "rsync 失败：请检查目标路径权限"
        exit 1
    }

    if [[ "$dry_run" == true ]]; then
        echo
        log_ok "Dry run complete — no files were transferred."
        log_warn "检查列表：不得出现 data/ logs/ config/*.cfg —— 确认后去掉 -d 正式部署"
    else
        # 部署盖章（唯一写入点，每次部署重盖）
        mkdir -p "$target/config"; echo production > "$target/config/env"
        log_ok "Deploy complete (stamped)."
        echo
        echo "On this machine:"
        echo "  cd ${target} && ./manage.sh rebuild"
    fi
}

run_deploy_data() {
    local dry_run=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--dry-run) dry_run=true ;;
            *) log_error "Unknown option: $1"; exit 1 ;;
        esac
        shift
    done

    local cfg="${SCRIPT_DIR}/config/deploy.cfg"
    if [[ ! -f "${cfg}" ]]; then
        log_error "config/deploy.cfg 不存在，请从 deploy.cfg.example 复制并配置"
        exit 1
    fi

    local ssh_target remote_path
    ssh_target="$(grep '^ssh_target' "${cfg}" | head -1 | cut -d '=' -f2 | xargs || true)"
    remote_path="$(grep '^remote_path' "${cfg}" | head -1 | cut -d '=' -f2 | xargs || true)"

    if [[ -z "${ssh_target}" || -z "${remote_path}" ]]; then
        log_error "deploy.cfg 中 ssh_target 或 remote_path 未配置"
        exit 1
    fi

    _check_deploy_target "${ssh_target}" || exit 1

    local exports_dir="$DATA_DIR/exports"
    local thumbs_dir="$DATA_DIR/thumbnails"

    if [[ ! -d "$exports_dir" ]] || [[ -z "$(ls -A "$exports_dir" 2>/dev/null)" ]]; then
        log_warn "No export files in $exports_dir."
        echo "Run '$0 export' first."
        exit 1
    fi

    local rsync_args=(-avz --progress --ignore-existing)
    if [[ "$dry_run" == true ]]; then
        rsync_args+=(--dry-run)
        log_step "[DRY RUN] Syncing data → ${ssh_target}:${remote_path}/data/ (append-only) …"
    else
        log_step "Syncing data → ${ssh_target}:${remote_path}/data/ (append-only) …"
    fi
    echo

    if [[ "$dry_run" != true ]]; then
        ssh "$ssh_target" "mkdir -p '$remote_path/data/exports' '$remote_path/data/thumbnails'" || {
            log_error "SSH 连接失败：无法创建远程数据目录"
            exit 1
        }
    fi

    log_info "exports (JSON) …"
    rsync "${rsync_args[@]}" "$exports_dir/" "${ssh_target}:${remote_path}/data/exports/" || {
        log_error "rsync 失败（exports）：请检查网络连接或远程路径权限"
        exit 1
    }

    if [[ -d "$thumbs_dir" ]] && [[ -n "$(ls -A "$thumbs_dir" 2>/dev/null)" ]]; then
        echo
        log_info "thumbnails (JPG) …"
        rsync "${rsync_args[@]}" "$thumbs_dir/" "${ssh_target}:${remote_path}/data/thumbnails/" || {
            log_error "rsync 失败（thumbnails）：请检查网络连接或远程路径权限"
            exit 1
        }
    fi

    echo
    if [[ "$dry_run" == true ]]; then
        log_ok "Dry run complete — no files were transferred."
    else
        log_ok "Data synced."
        echo "On the remote: cd ${remote_path} && ./manage.sh import <username>"
    fi
}

run_clean_exports() {
    local platform="${1:-}"
    local exports_dir="$DATA_DIR/exports"
    local pattern="*.json"

    if [[ -n "$platform" ]]; then
        pattern="${platform}_*.json"
    fi

    local count
    count=$(find "$exports_dir" -maxdepth 1 -name "$pattern" 2>/dev/null | wc -l | tr -d ' ')

    if [[ "$count" -eq 0 ]]; then
        log_info "No export files to clean${platform:+ (platform: $platform)}."
        return
    fi

    log_warn "This will delete $count export file(s)${platform:+ (platform: $platform)} in $exports_dir."
    read -r -p "Continue? [y/N] " confirm
    [[ "${confirm,,}" == "y" ]] || { log_info "Aborted."; exit 0; }

    find "$exports_dir" -maxdepth 1 -name "$pattern" -delete
    log_ok "Deleted $count export file(s)."
}

run_clean() {
    log_warn "This removes build artefacts: pip cache, __pycache__, frontend"
    log_warn "node_modules and dist. data/ logs/ config/ are NEVER touched."
    if pgrep -f "multi_listen.py" >/dev/null 2>&1; then
        log_error "Service is running — 'stop' it first (dist removal would 404 the SPA)."
        exit 1
    fi
    read -r -p "Continue? [y/N] " confirm
    [[ "${confirm,,}" == "y" ]] || { log_info "Aborted."; exit 0; }

    log_step "Cleaning …"
    [[ -x "$VENV_PY" ]] && "$VENV_PY" -m pip cache purge >/dev/null 2>&1 || true
    find "$SCRIPT_DIR/backend" -type d \( -name __pycache__ -o -name .pytest_cache -o -name "*.egg-info" \) \
        -prune -exec rm -rf {} + 2>/dev/null || true
    rm -rf "$SCRIPT_DIR/frontend/node_modules" "$DIST_DIR"
    log_ok "Clean complete."
}

# Generate the user LaunchAgent plist from the template (ROOT baked in),
# install it under ~/Library/LaunchAgents, and neutralize the legacy
# container-era watchdogs so they cannot fight the process stack.
# ── install-daemon / uninstall-daemon：生产托管显式安装（需 root 执行打印的命令）──
# 防呆是本命令的核心：Test 检出（无部署章）一律拒绝——Test 绝不托管给系统
#（2026-11-13 规约，主人拍板）。
run_install_daemon() {
    local src="$SCRIPT_DIR/deploy/$LAUNCHD_LABEL.plist"
    [[ -f "$src" ]] || { log_error "Missing plist template $src"; exit 1; }
    if is_prod_instance; then
        log_ok "生产实例（部署章在身）。安装命令（需 root，可用 ! 前缀执行）："
        echo "  sudo install -o root -g wheel -m 644 '$src' /Library/LaunchDaemons/$LAUNCHD_LABEL.plist"
        echo "  sudo launchctl bootstrap system /Library/LaunchDaemons/$LAUNCHD_LABEL.plist"
        log_info "托管后：restart 免 sudo（kill+KeepAlive）；真正停用用 sudo bootout。"
    else
        log_error "拒绝安装：本目录无部署章（config/env ≠ production）——这是 Test 检出，Test 不托管给系统。"
        log_error "生产副本由 deploy/deploy-local 盖章；盖章后在生产副本上执行本命令。"
        exit 1
    fi
}

run_uninstall_daemon() {
    if is_prod_instance; then
        log_ok "生产机卸载托管命令（需 root）："
        echo "  sudo launchctl bootout system/$LAUNCHD_LABEL 2>/dev/null"
        echo "  sudo rm /Library/LaunchDaemons/$LAUNCHD_LABEL.plist"
        log_info "卸载后本目录 ./manage.sh start 即回到 Test 形态（nohup）。"
    else
        log_error "本目录无部署章（Test 检出本就不该有托管），无需卸载。"
        exit 1
    fi
}

# ── usage ─────────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $0 <command> [options]

Lifecycle (dual form; no containers):
  init                       Create config, directories
  start                      Managed prod copy: no-op (KeepAlive) / bootstrap
                             when previously stopped; Test checkout: nohup
  stop                       Managed prod copy: svc-ctl bootout true-stop
                             (honest refusal when helper missing); Test: kill
  restart                    Managed prod copy: kill + KeepAlive respawn
                             (no sudo); Test: stop + start; health gates exit 0
  rebuild                    Reinstall backend deps (venv) + rebuild frontend, restart
  status                     Process + health checks (both ports) + data health
  install-daemon             Print root commands to install the system
                             LaunchDaemon (refused on Test checkouts without
                             the production stamp)
  uninstall-daemon           Print root commands to unload and remove the
                             LaunchDaemon
  clean                      Remove pip cache / __pycache__ / node_modules / dist

Data:
  export [youtube|bilibili]        Export video data to data/exports/
  import <user> [admin]            Import from data/exports/ (skip existing)
  clean-exports [youtube|bilibili] Delete exported JSON files (all or by platform)

Deploy (between machines):
  deploy [-d]                          Sync source code to remote (config/deploy.cfg),
                                       stamp config/env=production on the target
  deploy-data [-d]                     Append-only sync of exports + thumbnails
    -d, --dry-run                        Preview what would be transferred
                                         Exclusion rules:    $DEPLOY_EXCLUDE_FILE

Deploy (same machine, local rsync):
  deploy-local [-d] [path]             Sync source code to local prod dir
                                       (default $HOME/Services/vedio-split-view; must exist),
                                       stamp config/env=production

Examples:
  $0 rebuild                              # reinstall deps + rebuild frontend + restart

  $0 deploy -d                            # dry run — preview code sync
  $0 deploy                               # push code, then rebuild on remote
  $0 deploy-local -d                      # dry run — preview same-machine sync
  $0 deploy-data -d                       # dry run — preview data sync
  $0 export && $0 deploy-data             # export + push data, then import on remote
EOF
}

# ── main ──────────────────────────────────────────────────────────────────────
main() {
    local cmd="${1:-}"
    [[ $# -gt 0 ]] && shift || true
    case "$cmd" in
        init)        run_init ;;
        start)       run_start ;;
        stop)        run_stop ;;
        restart)     run_restart ;;
        rebuild)     run_rebuild "$@" ;;
        status)           run_status ;;
        install-daemon)   run_install_daemon ;;
        uninstall-daemon) run_uninstall_daemon ;;
        export)      run_export "$@" ;;
        import)      run_import "$@" ;;
        backup)      run_backup "$@" ;;
        deploy)        run_deploy "$@" ;;
        deploy-local)  run_deploy_local "$@" ;;
        deploy-data)   run_deploy_data "$@" ;;
        clean-exports) run_clean_exports "$@" ;;
        clean)         run_clean ;;
        *)           usage; exit 1 ;;
    esac
}

main "$@"
