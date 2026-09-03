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
COMPOSE_FILE="compose.yaml"
DATA_DIR="./data"
CONFIG_DIR="./config"
CONFIG_FILE="$CONFIG_DIR/app.yaml"
CONFIG_EXAMPLE="$CONFIG_DIR/app.yaml.example"
DEPLOY_CONFIG_FILE="$CONFIG_DIR/deploy.cfg"
DEPLOY_CONFIG_EXAMPLE="$CONFIG_DIR/deploy.cfg.example"
BUILD_CONFIG_FILE="$CONFIG_DIR/build.cfg"
BUILD_CONFIG_EXAMPLE="$CONFIG_DIR/build.cfg.example"
CERTS_DIR="$CONFIG_DIR/certs"  # no longer generated; dir kept in .rsync-exclude
REGISTRY="${REGISTRY:-}"
PODMAN_CMD="${PODMAN_CMD:-podman}"
PODMAN_DNS="${PODMAN_DNS:-}"
BUILD_CONFIG_LOADED=0

DEPLOY_EXCLUDE_FILE=".rsync-exclude"
NETWORK_NAME="vsplit-net"
BACKEND_CONTAINER="vsplit-backend"
FRONTEND_CONTAINER="vsplit-frontend"
BACKEND_IMAGE="vedio-split-view_backend"
FRONTEND_IMAGE="vedio-split-view_frontend"

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

_read_cfg_value() {
    local file="$1" key="$2" default="$3" val=""
    if [[ -f "$file" ]]; then
        val=$(grep -E "^${key}[[:space:]]*=" "$file" | head -1 | cut -d '=' -f2- | sed 's/[[:space:]]*#.*//' | xargs || true)
    fi
    echo "${val:-$default}"
}

read_deploy_value() { _read_cfg_value "$DEPLOY_CONFIG_FILE" "$1" "$2"; }

# Build-time sources (registry / apt / pypi) live in config/build.cfg, not in
# deploy.cfg: deploy.cfg is rsync-excluded (it holds the SSH target), so the
# remote never receives it and a build there would fall back to defaults that
# are unreachable from some networks. build.cfg carries no secrets and IS
# synced, keeping local and production builds on the same configured mirrors.
read_build_value() { _read_cfg_value "$BUILD_CONFIG_FILE" "$1" "$2"; }

# Env vars win over build.cfg, which wins over the built-in defaults.
load_build_config() {
    [[ "$BUILD_CONFIG_LOADED" -eq 1 ]] && return
    REGISTRY="${REGISTRY:-$(read_build_value registry "docker.io")}"
    PODMAN_DNS="${PODMAN_DNS:-$(read_build_value podman_dns "")}"
    BUILD_CONFIG_LOADED=1
}

get_app_port()      { read_yaml_value "app.port" "8080"; }
get_frontend_port() { read_yaml_value "app.frontend_port" "5180"; }
get_admin_password() { read_yaml_value "admin.password" ""; }

ensure_config() {
    [[ -f "$CONFIG_FILE" ]] || { log_error "Config not found. Run: $0 init"; exit 1; }
}

ensure_podman() {
    command -v "$PODMAN_CMD" &>/dev/null || {
        log_error "Podman not found. Install podman or set PODMAN_CMD=/path/to/podman."
        exit 1
    }
}

export_compose_env() {
    load_build_config
    export APP_PORT="$(get_app_port)"
    export FRONTEND_PORT="$(get_frontend_port)"
    export REGISTRY
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

_ensure_podman_network() {
    ensure_podman
    "$PODMAN_CMD" network exists "$NETWORK_NAME" 2>/dev/null || "$PODMAN_CMD" network create "$NETWORK_NAME" >/dev/null
}

_ensure_images_exist() {
    ensure_podman
    local missing=()
    "$PODMAN_CMD" image exists "$BACKEND_IMAGE" 2>/dev/null || missing+=("$BACKEND_IMAGE")
    "$PODMAN_CMD" image exists "$FRONTEND_IMAGE" 2>/dev/null || missing+=("$FRONTEND_IMAGE")
    if [[ "${#missing[@]}" -gt 0 ]]; then
        log_error "Missing image(s): ${missing[*]}"
        echo "Run: $0 rebuild"
        exit 1
    fi
}

_remove_container_if_exists() {
    local name="$1"
    if "$PODMAN_CMD" container exists "$name" 2>/dev/null; then
        "$PODMAN_CMD" rm -f "$name" >/dev/null
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

run_start() {
    ensure_config
    export_compose_env
    _ensure_images_exist
    _ensure_podman_network
    mkdir -p logs/nginx
    log_step "Starting services …"

    _remove_container_if_exists "$FRONTEND_CONTAINER"
    _remove_container_if_exists "$BACKEND_CONTAINER"

    # Do not pass --dns here. On a dns-enabled network it never reaches
    # resolv.conf; aardvark swallows it as the *only* upstream, so one UDP/53
    # blip becomes EAI_AGAIN for every outbound host. Unset --dns lets
    # aardvark forward via pasta (169.254.1.1) then the VM resolver.
    "$PODMAN_CMD" run -d \
        --name "$BACKEND_CONTAINER" \
        --network "$NETWORK_NAME" \
        --network-alias backend \
        -p "$APP_PORT:8080" \
        -v "$SCRIPT_DIR/config:/app/config:ro" \
        -v "$SCRIPT_DIR/data:/app/data" \
        -v "$SCRIPT_DIR/logs:/app/logs" \
        -e PORT=8080 \
        -e http_proxy= \
        -e https_proxy= \
        -e HTTP_PROXY= \
        -e HTTPS_PROXY= \
        --restart unless-stopped \
        "$BACKEND_IMAGE" >/dev/null

    "$PODMAN_CMD" run -d \
        --name "$FRONTEND_CONTAINER" \
        --network "$NETWORK_NAME" \
        --network-alias frontend \
        -p "$FRONTEND_PORT:80" \
        -v "$SCRIPT_DIR/logs/nginx:/var/log/nginx" \
        --restart unless-stopped \
        "$FRONTEND_IMAGE" >/dev/null

    log_ok "Backend: http://localhost:$APP_PORT  Frontend: http://localhost:$FRONTEND_PORT"
}

run_stop() {
    ensure_podman
    log_step "Stopping services …"
    _remove_container_if_exists "$FRONTEND_CONTAINER"
    _remove_container_if_exists "$BACKEND_CONTAINER"
    log_ok "Stopped."
}

run_restart() { run_stop; run_start; }

run_rebuild() {
    local no_cache="" pull=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -n|--no-cache) no_cache="--no-cache" ;;
            -p|--pull)     pull="--pull-always" ;;
            *) log_error "Unknown option: $1"; exit 1 ;;
        esac
        shift
    done

    ensure_config
    export_compose_env
    ensure_podman

    log_step "Building images (registry: $REGISTRY) …"
    [[ -n "$PODMAN_DNS" ]] && log_info "podman DNS: $PODMAN_DNS"
    [[ -n "$no_cache" ]] && log_info "--no-cache"
    [[ -n "$pull" ]]     && log_info "--pull"

    local use_proxy=0
    if [[ -n "${http_proxy:-}" ]] || [[ -n "${https_proxy:-}" ]]; then
        use_proxy=1
        log_info "proxy detected → podman build --network=host"
    fi

    local apt_mirror pypi_index pypi_trusted_host
    apt_mirror="$(read_build_value apt_mirror "deb.debian.org")"
    pypi_index="$(read_build_value pypi_index "https://pypi.org/simple/")"
    pypi_trusted_host="$(read_build_value pypi_trusted_host "")"
    log_info "build sources: apt=$apt_mirror pypi=$pypi_index"

    for svc in backend frontend; do
        log_step "Building $svc …"
        local image_tag="vedio-split-view_${svc}"

        local build_args=(--build-arg "REGISTRY=$REGISTRY")
        if [[ "$svc" == "backend" ]]; then
            build_args+=(
                --build-arg "APT_MIRROR=$apt_mirror"
                --build-arg "PYPI_INDEX=$pypi_index"
                --build-arg "PYPI_TRUSTED_HOST=$pypi_trusted_host"
            )
        fi
        [[ -n "$PODMAN_DNS" ]] && build_args+=(--dns "$PODMAN_DNS")
        [[ -n "$no_cache" ]] && build_args+=(--no-cache)
        [[ -n "$pull" ]]     && build_args+=(--pull=always)

        if [[ "$use_proxy" -eq 1 ]]; then
            build_args+=(--network=host)
            [[ -n "${http_proxy:-}" ]]  && build_args+=(--build-arg "http_proxy=$http_proxy")
            [[ -n "${https_proxy:-}" ]] && build_args+=(--build-arg "https_proxy=$https_proxy")
        fi

        "$PODMAN_CMD" build "${build_args[@]}" -t "$image_tag" "./${svc}"

        if ! "$PODMAN_CMD" image exists "$image_tag" 2>/dev/null; then
            log_error "$svc image not found after build."
            exit 1
        fi
        log_ok "$svc"
    done

    log_step "Restarting services …"
    _remove_container_if_exists "$FRONTEND_CONTAINER"
    _remove_container_if_exists "$BACKEND_CONTAINER"
    run_start

    echo
    _smoke_check "$APP_PORT" "$(get_admin_password)"

    log_ok "Done. Backend: http://localhost:$APP_PORT  Frontend: http://localhost:$FRONTEND_PORT"
}

run_status() {
    ensure_podman
    export_compose_env
    local fail=0
    log_step "Container status:"
    "$PODMAN_CMD" ps -a --filter "name=vsplit-"
    echo

    if curl -sf --noproxy '*' "http://localhost:$APP_PORT/health" >/dev/null 2>&1; then
        log_ok "URL: http://localhost:$APP_PORT"
        log_ok "Health: OK"
    else
        log_ok "URL: http://localhost:$APP_PORT"
        log_error "Health: FAILED"
        fail=1
    fi
    if curl -sf --noproxy '*' "http://localhost:$FRONTEND_PORT/" >/dev/null 2>&1; then
        log_ok "URL: http://localhost:$FRONTEND_PORT"
        log_ok "Health: OK"
    else
        log_ok "URL: http://localhost:$FRONTEND_PORT"
        log_error "Health: FAILED"
        fail=1
    fi

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

    _check_deploy_target "${ssh_target}" || exit 1

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
        log_ok "Deploy complete."
        echo
        echo "On the remote:"
        echo "  cd ${remote_path} && ./manage.sh rebuild"
        echo "  cd ${remote_path} && ./manage.sh install-launchd   # once, needs sudo"
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
    ensure_podman
    log_warn "This will remove dangling images and build cache."
    log_warn "PRUNE SAFETY: 'podman system prune' releases file descriptors held"
    log_warn "  by stopped containers. If a container was writing to a bind-mount"
    log_warn "  whose host directory was deleted, its data lives only in that fd —"
    log_warn "  pruning makes it disappear permanently. This is exactly how the"
    log_warn "  production SQLite DB was lost. NEVER prune while data-bearing"
    log_warn "  containers are stopped; verify the list below first."
    echo
    log_step "Stopped containers (must be safe to discard before pruning):"
    # Never abort status listing under set -e.
    "$PODMAN_CMD" ps -a --filter "status=exited" \
        --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null || true
    echo
    log_warn "Confirm none of the above still hold unflushed data (e.g. vsplit-backend"
    log_warn "  writing to data/video_split.db). If unsure, 'start' them first, then quit."
    read -r -p "Continue with prune? [y/N] " confirm
    [[ "${confirm,,}" == "y" ]] || { log_info "Aborted."; exit 0; }

    log_step "Pruning images …"
    "$PODMAN_CMD" image prune -f
    log_step "Pruning system (containers/networks/build cache; volumes NOT touched) …"
    "$PODMAN_CMD" system prune -f
    log_ok "Clean complete."
}

# Bake SCRIPT_DIR / current user into the LaunchDaemon plist and install it
# under /Library/LaunchDaemons (needs sudo). Without passwordless sudo, print
# the commands — same one-shot pattern as com.koven.multica.
run_install_launchd() {
    local label="com.koven.vedio-split-view"
    local src="$SCRIPT_DIR/scripts/${label}.plist"
    local dest="/Library/LaunchDaemons/${label}.plist"
    local generated="$SCRIPT_DIR/logs/${label}.plist"
    local user
    user="$(whoami)"

    [[ -f "$src" ]] || { log_error "missing $src"; exit 1; }
    mkdir -p "$SCRIPT_DIR/logs"
    sed \
        -e "s|/Users/admin/Services/vedio-split-view|${SCRIPT_DIR}|g" \
        -e "s|<string>admin</string>|<string>${user}</string>|" \
        "$src" > "$generated"
    chmod +x "$SCRIPT_DIR/scripts/launchd-start.sh"
    log_info "Plist: $generated"

    if sudo -n true 2>/dev/null; then
        sudo cp "$generated" "$dest"
        sudo chown root:wheel "$dest"
        sudo chmod 644 "$dest"
        sudo launchctl bootout "system/${label}" 2>/dev/null || true
        sudo launchctl bootstrap system "$dest"
        log_ok "LaunchDaemon ${label} installed"
        return 0
    fi

    log_warn "需要 sudo 才能写入 /Library/LaunchDaemons/。在本机执行："
    echo "  sudo cp $generated $dest"
    echo "  sudo chown root:wheel $dest"
    echo "  sudo chmod 644 $dest"
    echo "  sudo launchctl bootout system/${label} 2>/dev/null || true"
    echo "  sudo launchctl bootstrap system $dest"
    return 1
}

# ── usage ─────────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $0 <command> [options]

Lifecycle:
  init                       Create config, directories
  start                      Start all services
  stop                       Stop all services
  restart                    Stop + start
  rebuild [-n] [-p]          Build images and restart
    -n, --no-cache             Ignore layer cache
    -p, --pull                 Re-pull base images
  status                     Show container status + health check
  install-launchd            Install macOS LaunchDaemon (needs sudo once)
  clean                      Remove dangling images / build cache

Data:
  export [youtube|bilibili]        Export video data to data/exports/
  import <user> [admin]            Import from data/exports/ (skip existing)
  clean-exports [youtube|bilibili] Delete exported JSON files (all or by platform)

Deploy (between machines):
  deploy [-d]                          Sync source code to remote (config/deploy.cfg)
  deploy-data [-d]                     Append-only sync of exports + thumbnails
    -d, --dry-run                        Preview what would be transferred
                                         Exclusion rules:    $DEPLOY_EXCLUDE_FILE

Examples:
  $0 rebuild                              # build and restart
  $0 rebuild -n                           # no cache
  REGISTRY=docker.io $0 rebuild           # Docker Hub instead of CN mirror

  $0 deploy -d                            # dry run — preview code sync
  $0 deploy                               # push code, then rebuild on remote
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
        install-launchd)  run_install_launchd ;;
        export)      run_export "$@" ;;
        import)      run_import "$@" ;;
        backup)      run_backup "$@" ;;
        deploy)        run_deploy "$@" ;;
        deploy-data)   run_deploy_data "$@" ;;
        clean-exports) run_clean_exports "$@" ;;
        clean)         run_clean ;;
        *)           usage; exit 1 ;;
    esac
}

main "$@"
