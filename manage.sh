#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[info]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
log_error() { echo -e "${RED}[error]${NC} $*"; }
log_step()  { echo -e "${BLUE}[step]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[ok]${NC}    $*"; }

# ── constants ─────────────────────────────────────────────────────────────────
COMPOSE_FILE="compose.yaml"
DATA_DIR="./data"
CONFIG_DIR="./config"
CONFIG_FILE="$CONFIG_DIR/app.yaml"
CONFIG_EXAMPLE="$CONFIG_DIR/app.yaml.example"
DEPLOY_CONFIG_FILE="$CONFIG_DIR/deploy.cfg"
CERTS_DIR="$CONFIG_DIR/certs"
REGISTRY="${REGISTRY:-docker.m.daocloud.io}"
PODMAN_CMD="${PODMAN_CMD:-podman}"

DEPLOY_EXCLUDE_FILE=".rsync-exclude"
DEPLOY_DEFAULT_REMOTE_DIR="ai/vedio-split-view"
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

get_app_port()      { read_yaml_value "app.port" "8080"; }
get_frontend_port() { read_yaml_value "app.frontend_port" "5180"; }

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

# ── python cache / build ─────────────────────────────────────────────────────
backend/.venv/
__pycache__/
*.pyc
*.pyo
*.egg-info/
.pytest_cache/

# ── frontend cache / build ───────────────────────────────────────────────────
frontend/node_modules/
frontend/dist/

# ── test fixtures (large audio / video) ──────────────────────────────────────
test/
test_transcribe.py

# ── CI / tooling artefacts ───────────────────────────────────────────────────
.playwright-cli/
.mirror_state

# ── sensitive config (never overwrite remote secrets) ────────────────────────
config/app.yaml
config/certs/
config/*_cookies.txt

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
        cat > "$DEPLOY_CONFIG_FILE" <<'CFG'
# deploy target for manage.sh deploy / deploy-data
# Command-line args still override these values.

# Example: root@your-server
DEPLOY_REMOTE=""

# Remote path relative to the SSH user's home directory.
DEPLOY_REMOTE_DIR="ai/vedio-split-view"
CFG
        log_info "Created $DEPLOY_CONFIG_FILE (edit DEPLOY_REMOTE before deploying)"
    fi
}

_load_deploy_config() {
    [[ -f "$DEPLOY_CONFIG_FILE" ]] || return 0
    # shellcheck source=/dev/null
    source "$DEPLOY_CONFIG_FILE"
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

# ── generate certs ────────────────────────────────────────────────────────────
_generate_certs() {
    if [[ -f "$CERTS_DIR/cert.pem" ]] && [[ -f "$CERTS_DIR/key.pem" ]]; then
        log_info "Certificates already exist in $CERTS_DIR"
        return 0
    fi
    if ! command -v mkcert &>/dev/null; then
        log_error "mkcert not found. Install: brew install mkcert (macOS)"
        return 1
    fi
    mkdir -p "$CERTS_DIR"
    log_step "Generating TLS certificates …"
    mkcert -cert-file "$CERTS_DIR/cert.pem" -key-file "$CERTS_DIR/key.pem" \
        localhost 127.0.0.1 ::1
    log_ok "Certificates generated in $CERTS_DIR"
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

    _generate_certs
    _ensure_deploy_exclude
    _ensure_deploy_config
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
        -p "$FRONTEND_PORT:443" \
        -v "$SCRIPT_DIR/config/certs:/etc/nginx/certs:ro" \
        -v "$SCRIPT_DIR/logs/nginx:/var/log/nginx" \
        --restart unless-stopped \
        "$FRONTEND_IMAGE" >/dev/null

    log_ok "Backend: http://localhost:$APP_PORT  Frontend: https://localhost:$FRONTEND_PORT"
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
    [[ -n "$no_cache" ]] && log_info "--no-cache"
    [[ -n "$pull" ]]     && log_info "--pull"

    local use_proxy=0
    if [[ -n "${http_proxy:-}" ]] || [[ -n "${https_proxy:-}" ]]; then
        use_proxy=1
        log_info "proxy detected → podman build --network=host"
    fi

    for svc in backend frontend; do
        log_step "Building $svc …"
        local image_tag="vedio-split-view_${svc}"

        if [[ "$use_proxy" -eq 1 ]]; then
            local args="--network=host --build-arg REGISTRY=$REGISTRY"
            [[ -n "${http_proxy:-}" ]]  && args="$args --build-arg http_proxy=$http_proxy"
            [[ -n "${https_proxy:-}" ]] && args="$args --build-arg https_proxy=$https_proxy"
            [[ -n "$no_cache" ]] && args="$args --no-cache"
            "$PODMAN_CMD" build $args -t "$image_tag" "./${svc}"
        else
            local build_args=(--build-arg "REGISTRY=$REGISTRY")
            [[ -n "$no_cache" ]] && build_args+=(--no-cache)
            [[ -n "$pull" ]]     && build_args+=(--pull=always)
            "$PODMAN_CMD" build "${build_args[@]}" -t "$image_tag" "./${svc}"
        fi

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

    log_ok "Done. Backend: http://localhost:$APP_PORT  Frontend: https://localhost:$FRONTEND_PORT"
}

run_status() {
    ensure_podman
    export_compose_env
    log_step "Container status:"
    "$PODMAN_CMD" ps -a --filter "name=vsplit-"
    echo

    log_step "Health check …"
    if curl -sf --noproxy '*' "http://localhost:$APP_PORT/api/health" >/dev/null 2>&1; then
        log_ok "Backend  :$APP_PORT"
    else
        log_error "Backend  :$APP_PORT  not responding"
    fi
    if curl -skf --noproxy '*' "https://localhost:$FRONTEND_PORT/" >/dev/null 2>&1; then
        log_ok "Frontend :$FRONTEND_PORT"
    else
        log_error "Frontend :$FRONTEND_PORT  not responding"
    fi
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
    _ensure_deploy_config
    _load_deploy_config

    local dry_run=false remote="${DEPLOY_REMOTE:-}" remote_dir="${DEPLOY_REMOTE_DIR:-$DEPLOY_DEFAULT_REMOTE_DIR}"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--dry-run) dry_run=true ;;
            *)
                if [[ -z "$remote" ]]; then remote="$1"
                else remote_dir="$1"; fi
                ;;
        esac
        shift
    done

    if [[ -z "$remote" ]]; then
        cat <<EOF
Usage: $0 deploy [-d] <user@host> [remote_dir]

  Sync source code to remote (no data, no secrets).
  Config file:        $DEPLOY_CONFIG_FILE
  Default remote:     DEPLOY_REMOTE=${DEPLOY_REMOTE:-}
  Default remote dir: ~/$remote_dir
  Exclusion rules:    $DEPLOY_EXCLUDE_FILE (edit freely)

Options:
  -d, --dry-run   Show what would be transferred without actually doing it

After deploy, on the remote:
  1. cp config/app.yaml.example config/app.yaml   # first time
  2. ./manage.sh rebuild
EOF
        exit 1
    fi

    _ensure_deploy_exclude

    local rsync_opts=(-avz --progress --delete --exclude-from="$DEPLOY_EXCLUDE_FILE")
    if [[ "$dry_run" == true ]]; then
        rsync_opts+=(--dry-run)
        log_step "[DRY RUN] Deploying code → ${remote}:${remote_dir} …"
    else
        log_step "Deploying code → ${remote}:${remote_dir} …"
    fi
    log_info "Exclusion rules: $DEPLOY_EXCLUDE_FILE"
    log_info "Config file: $DEPLOY_CONFIG_FILE"

    if [[ "$dry_run" != true ]]; then
        ssh "$remote" "mkdir -p '$remote_dir'"
    fi

    rsync "${rsync_opts[@]}" ./ "${remote}:${remote_dir}/"

    if [[ "$dry_run" == true ]]; then
        echo
        log_ok "Dry run complete — no files were transferred."
    else
        log_ok "Deploy complete."
        echo
        echo "On the remote:"
        echo "  cd ~/$remote_dir && ./manage.sh rebuild"
    fi
}

run_deploy_data() {
    _ensure_deploy_config
    _load_deploy_config

    local dry_run=false remote="${DEPLOY_REMOTE:-}" remote_dir="${DEPLOY_REMOTE_DIR:-$DEPLOY_DEFAULT_REMOTE_DIR}"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--dry-run) dry_run=true ;;
            *)
                if [[ -z "$remote" ]]; then remote="$1"
                else remote_dir="$1"; fi
                ;;
        esac
        shift
    done

    if [[ -z "$remote" ]]; then
        cat <<EOF
Usage: $0 deploy-data [-d] <user@host> [remote_dir]

  Append-only sync of exports (JSON) + thumbnails (JPG) to remote.
  Only adds files that don't exist on the remote (no overwrite).
  Config file:        $DEPLOY_CONFIG_FILE
  Default remote:     DEPLOY_REMOTE=${DEPLOY_REMOTE:-}
  Default remote dir: ~/$remote_dir

Options:
  -d, --dry-run   Show what would be transferred without actually doing it

Typical workflow:
  Local:   $0 export && $0 deploy-data user@host
  Remote:  $0 import <username>
EOF
        exit 1
    fi

    local exports_dir="$DATA_DIR/exports"
    local thumbs_dir="$DATA_DIR/thumbnails"

    if [[ ! -d "$exports_dir" ]] || [[ -z "$(ls -A "$exports_dir" 2>/dev/null)" ]]; then
        log_warn "No export files in $exports_dir."
        echo "Run '$0 export' first."
        exit 1
    fi

    local rsync_opts=(-avz --progress --ignore-existing)
    if [[ "$dry_run" == true ]]; then
        rsync_opts+=(--dry-run)
        log_step "[DRY RUN] Syncing data → ${remote}:${remote_dir}/data/ (append-only) …"
    else
        log_step "Syncing data → ${remote}:${remote_dir}/data/ (append-only) …"
    fi
    echo

    if [[ "$dry_run" != true ]]; then
        ssh "$remote" "mkdir -p '$remote_dir/data/exports' '$remote_dir/data/thumbnails'"
    fi

    log_info "exports (JSON) …"
    rsync "${rsync_opts[@]}" "$exports_dir/" "${remote}:${remote_dir}/data/exports/"

    if [[ -d "$thumbs_dir" ]] && [[ -n "$(ls -A "$thumbs_dir" 2>/dev/null)" ]]; then
        echo
        log_info "thumbnails (JPG) …"
        rsync "${rsync_opts[@]}" "$thumbs_dir/" "${remote}:${remote_dir}/data/thumbnails/"
    fi

    echo
    if [[ "$dry_run" == true ]]; then
        log_ok "Dry run complete — no files were transferred."
    else
        log_ok "Data synced."
        echo "On the remote: cd ~/$remote_dir && ./manage.sh import <username>"
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
    read -r -p "Continue? [y/N] " confirm
    [[ "${confirm,,}" == "y" ]] || { log_info "Aborted."; exit 0; }

    log_step "Pruning images …"
    "$PODMAN_CMD" image prune -f
    log_step "Pruning system …"
    "$PODMAN_CMD" system prune -f
    log_ok "Clean complete."
}

# ── usage ─────────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $0 <command> [options]

Lifecycle:
  init                       Create config, certs, directories
  start                      Start all services
  stop                       Stop all services
  restart                    Stop + start
  rebuild [-n] [-p]          Build images and restart
    -n, --no-cache             Ignore layer cache
    -p, --pull                 Re-pull base images
  status                     Show container status + health check
  clean                      Remove dangling images / build cache

Data:
  export [youtube|bilibili]        Export video data to data/exports/
  import <user> [admin]            Import from data/exports/ (skip existing)
  clean-exports [youtube|bilibili] Delete exported JSON files (all or by platform)

Deploy (between machines):
  deploy [-d] [user@host] [dir]        Sync source code to remote (no data/secrets)
  deploy-data [-d] [user@host] [dir]   Append-only sync of exports + thumbnails
    -d, --dry-run                        Preview what would be transferred
                                         Config file:        $DEPLOY_CONFIG_FILE
                                         Default remote dir: ~/$DEPLOY_DEFAULT_REMOTE_DIR
                                         Exclusion rules:    $DEPLOY_EXCLUDE_FILE

Examples:
  $0 rebuild                              # build and restart
  $0 rebuild -n                           # no cache
  REGISTRY=docker.io $0 rebuild           # Docker Hub instead of CN mirror

  $0 deploy -d                            # dry run using config/deploy.cfg
  $0 deploy root@srv                      # override DEPLOY_REMOTE
  $0 deploy-data -d                       # dry run using config/deploy.cfg
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
        status)      run_status ;;
        export)      run_export "$@" ;;
        import)      run_import "$@" ;;
        deploy)        run_deploy "$@" ;;
        deploy-data)   run_deploy_data "$@" ;;
        clean-exports) run_clean_exports "$@" ;;
        clean)         run_clean ;;
        *)           usage; exit 1 ;;
    esac
}

main "$@"
