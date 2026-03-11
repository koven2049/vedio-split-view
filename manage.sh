#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="compose.yaml"
APP_NAME="vsplit"
DATA_DIR="./data"
CONFIG_DIR="./config"
CONFIG_FILE="$CONFIG_DIR/app.yaml"
CONFIG_EXAMPLE="$CONFIG_DIR/app.yaml.example"
MIRROR_STATE_FILE="$SCRIPT_DIR/.mirror_state"

DEFAULT_REGISTRY="docker.m.daocloud.io"

read_yaml_value() {
    local key="$1"
    local default="$2"
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "$default"
        return
    fi
    local section="${key%%.*}"
    local field="${key#*.}"
    local in_section=0
    local val=""
    while IFS= read -r line; do
        if echo "$line" | grep -qE "^${section}:"; then
            in_section=1
            continue
        fi
        if [ $in_section -eq 1 ] && echo "$line" | grep -qE "^[a-zA-Z]"; then
            break
        fi
        if [ $in_section -eq 1 ] && echo "$line" | grep -qE "^[[:space:]]+${field}:"; then
            val=$(echo "$line" | sed "s/^[^:]*:[[:space:]]*//" | sed 's/[[:space:]]*#.*//' | sed 's/^"//' | sed 's/"$//')
            break
        fi
    done < "$CONFIG_FILE"
    echo "${val:-$default}"
}

get_app_port() {
    read_yaml_value "app.port" "8080"
}

get_frontend_port() {
    read_yaml_value "app.frontend_port" "5180"
}

get_current_mirror() {
    if [ -f "$MIRROR_STATE_FILE" ]; then
        cat "$MIRROR_STATE_FILE"
    else
        echo "cn"
    fi
}

get_registry() {
    MODE=$(get_current_mirror)
    if [ "$MODE" = "cn" ]; then
        echo "$DEFAULT_REGISTRY"
    else
        echo "docker.io"
    fi
}

CERTS_DIR="$CONFIG_DIR/certs"

generate_certs() {
    if [ -f "$CERTS_DIR/cert.pem" ] && [ -f "$CERTS_DIR/key.pem" ]; then
        echo "Certificates already exist in $CERTS_DIR"
        return 0
    fi

    if ! command -v mkcert &>/dev/null; then
        echo -e "\033[31mmkcert not found. Install it first:\033[0m"
        echo "  macOS:  brew install mkcert"
        echo "  Linux:  https://github.com/FiloSottile/mkcert#installation"
        echo ""
        echo "Then run: mkcert -install"
        return 1
    fi

    mkdir -p "$CERTS_DIR"
    echo "Generating TLS certificates with mkcert..."
    mkcert -cert-file "$CERTS_DIR/cert.pem" -key-file "$CERTS_DIR/key.pem" \
        localhost 127.0.0.1 ::1
    echo "Certificates generated in $CERTS_DIR"
    echo ""
    echo "If browsers still show warnings, run once:"
    echo "  mkcert -install"
}

init() {
    echo "=== Initializing project ==="
    mkdir -p "$DATA_DIR/tmp"

    if [ ! -f "$CONFIG_FILE" ]; then
        cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
        echo "Config created: $CONFIG_FILE"
        echo "Please edit the config file before starting the service."
    else
        echo "Config already exists: $CONFIG_FILE"
    fi

    generate_certs

    echo "Initialization complete."
}

start() {
    echo "=== Starting services ==="
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "Config not found. Run: $0 init"
        exit 1
    fi

    local PORT=$(get_app_port)
    local FPORT=$(get_frontend_port)

    APP_PORT=$PORT FRONTEND_PORT=$FPORT REGISTRY=$(get_registry) \
        podman-compose -f "$COMPOSE_FILE" up -d

    echo "Services started."
    echo "  Backend:  http://localhost:$PORT"
    echo "  Frontend: https://localhost:$FPORT"
    echo "  API Docs: http://localhost:$PORT/docs"
}

stop() {
    echo "=== Stopping services ==="
    podman-compose -f "$COMPOSE_FILE" down
    echo "Services stopped."
}

restart() {
    stop
    start
}

rebuild() {
    REGISTRY=$(get_registry)
    local PORT=$(get_app_port)
    local FPORT=$(get_frontend_port)

    local NO_CACHE=""
    local PULL=""
    local SERVICE=""

    for arg in "$@"; do
        case "$arg" in
            --no-cache|-n)  NO_CACHE="--no-cache" ;;
            --pull|-p)      PULL="--pull-always" ;;
            backend|frontend) SERVICE="$arg" ;;
        esac
    done

    echo "=== Building images (registry: $REGISTRY) ==="
    [ -n "$NO_CACHE" ] && echo "  --no-cache: ignoring layer cache"
    [ -n "$PULL" ]     && echo "  --pull:     re-pulling base images"
    [ -n "$SERVICE" ]  && echo "  service:    $SERVICE only"
    echo ""

    local SERVICES_TO_BUILD
    if [ -n "$SERVICE" ]; then
        SERVICES_TO_BUILD="$SERVICE"
    else
        SERVICES_TO_BUILD="backend frontend"
    fi

    local USE_PROXY=0
    if [ -n "${http_proxy:-}" ] || [ -n "${https_proxy:-}" ]; then
        USE_PROXY=1
        echo "  proxy detected, will use --network=host via podman build directly"
        echo ""
    fi

    for svc in $SERVICES_TO_BUILD; do
        echo "--- Building $svc ---"
        local IMAGE_TAG="vedio-split-view_${svc}"

        if [ "$USE_PROXY" -eq 1 ]; then
            local PODMAN_ARGS="--network=host --build-arg REGISTRY=$REGISTRY"
            [ -n "${http_proxy:-}" ]  && PODMAN_ARGS="$PODMAN_ARGS --build-arg http_proxy=$http_proxy"
            [ -n "${https_proxy:-}" ] && PODMAN_ARGS="$PODMAN_ARGS --build-arg https_proxy=$https_proxy"
            [ -n "$NO_CACHE" ] && PODMAN_ARGS="$PODMAN_ARGS --no-cache"
            podman build $PODMAN_ARGS -t "$IMAGE_TAG" "./${svc}"
        else
            local COMPOSE_ARGS="--build-arg REGISTRY=$REGISTRY"
            [ -n "$NO_CACHE" ] && COMPOSE_ARGS="$COMPOSE_ARGS --no-cache"
            [ -n "$PULL" ]     && COMPOSE_ARGS="$COMPOSE_ARGS $PULL"
            REGISTRY=$REGISTRY podman-compose -f $COMPOSE_FILE build $COMPOSE_ARGS $svc
        fi

        if [ $? -ne 0 ]; then
            echo -e "\033[31m$svc build command failed!\033[0m"
            exit 1
        fi

        if ! podman image exists "$IMAGE_TAG" 2>/dev/null; then
            echo -e "\033[31m$svc image '$IMAGE_TAG' not found after build — build likely failed silently.\033[0m"
            exit 1
        fi
        echo -e "\033[32m$svc build OK\033[0m"
        echo ""
    done

    echo ""
    echo "=== Restarting services ==="

    podman-compose -f "$COMPOSE_FILE" down 2>/dev/null
    APP_PORT=$PORT FRONTEND_PORT=$FPORT REGISTRY=$REGISTRY \
        podman-compose -f "$COMPOSE_FILE" up -d

    if [ $? -ne 0 ]; then
        echo -e "\033[31mStart failed!\033[0m"
        exit 1
    fi

    echo -e "\033[32mBuild and start complete.\033[0m"
    echo "  Backend:  http://localhost:$PORT"
    echo "  Frontend: https://localhost:$FPORT"
    echo "  API Docs: http://localhost:$PORT/docs"
}

status() {
    local PORT=$(get_app_port)

    echo "=== Service Status ==="
    podman-compose -f "$COMPOSE_FILE" ps

    echo ""
    echo "=== Health Check ==="
    if curl -sf --noproxy '*' "http://localhost:$PORT/api/health" > /dev/null 2>&1; then
        echo -e "\033[32mBackend (port $PORT): healthy\033[0m"
    else
        echo -e "\033[31mBackend (port $PORT): not responding\033[0m"
    fi
}

logs() {
    SERVICE="${2:-}"
    if [ -n "$SERVICE" ]; then
        podman-compose -f "$COMPOSE_FILE" logs -f "$SERVICE"
    else
        podman-compose -f "$COMPOSE_FILE" logs -f
    fi
}

mirror() {
    case "$2" in
        cn)
            echo "cn" > "$MIRROR_STATE_FILE"
            echo "Switched to CN mirror: $DEFAULT_REGISTRY"
            ;;
        default)
            echo "default" > "$MIRROR_STATE_FILE"
            echo "Switched to default: docker.io"
            ;;
        status)
            MODE=$(get_current_mirror)
            if [ "$MODE" = "cn" ]; then
                echo "Current: CN ($DEFAULT_REGISTRY)"
            else
                echo "Current: default (docker.io)"
            fi
            ;;
        *)
            echo "Usage: $0 mirror {cn|default|status}"
            ;;
    esac
}

hotpatch() {
    local SERVICE="${1:-}"

    if [ -z "$SERVICE" ] || { [ "$SERVICE" != "backend" ] && [ "$SERVICE" != "frontend" ]; }; then
        echo "Usage: $0 hotpatch {backend|frontend}"
        echo ""
        echo "Quickly patch source code into a running container (seconds, no image rebuild)."
        echo "  backend  — copy .py files + restart uvicorn"
        echo "  frontend — rebuild dist inside container + reload nginx"
        echo ""
        echo "Use 'rebuild' instead when you changed dependencies (pyproject.toml / package.json)."
        exit 1
    fi

    if [ "$SERVICE" = "backend" ]; then
        echo "=== Hot-patching backend ==="
        local CONTAINER="vsplit-backend"

        if ! podman ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
            echo -e "\033[31m$CONTAINER is not running. Use 'rebuild backend' first.\033[0m"
            exit 1
        fi

        echo "  Copying source..."
        podman cp ./backend/src/video_split/. "${CONTAINER}:/app/src/video_split/"
        echo "  Restarting..."
        podman restart "$CONTAINER"
        echo -e "\033[32mBackend patched — done in seconds.\033[0m"

    elif [ "$SERVICE" = "frontend" ]; then
        echo "=== Hot-patching frontend ==="
        local CONTAINER="vsplit-frontend"

        if ! podman ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
            echo -e "\033[31m$CONTAINER is not running. Use 'rebuild frontend' first.\033[0m"
            exit 1
        fi

        local IMG="vedio-split-view_frontend"
        local DIST_DIR=$(mktemp -d)

        echo "  Building dist in temporary container..."
        podman run --rm \
            -v "$(pwd)/frontend/src:/app/src:ro" \
            -v "$(pwd)/frontend/index.html:/app/index.html:ro" \
            -v "${DIST_DIR}:/app/dist" \
            "$IMG" sh -c '
                cd /app 2>/dev/null
                PATH=/app/node_modules/.bin:$PATH
                if command -v tsc >/dev/null 2>&1; then
                    tsc -b && vite build
                elif [ -f node_modules/.bin/tsc ]; then
                    node_modules/.bin/tsc -b && node_modules/.bin/vite build
                else
                    echo "ERROR: tsc not found in image"
                    exit 1
                fi
            ' 2>&1

        if [ $? -ne 0 ] || [ ! -f "${DIST_DIR}/index.html" ]; then
            rm -rf "$DIST_DIR"
            echo -e "\033[33mFrontend hot-patch failed. Falling back to rebuild...\033[0m"
            rebuild frontend
            return
        fi

        echo "  Replacing nginx html..."
        podman cp "${DIST_DIR}/." "${CONTAINER}:/usr/share/nginx/html/"
        podman exec "$CONTAINER" nginx -s reload 2>/dev/null || podman restart "$CONTAINER"
        rm -rf "$DIST_DIR"
        echo -e "\033[32mFrontend patched — done.\033[0m"
    fi
}

export_data() {
    local PORT=$(get_app_port)
    local PLATFORM=""
    local ADMIN_USER="admin"
    local ADMIN_PASS=""

    for arg in "$@"; do
        case "$arg" in
            youtube|bilibili) PLATFORM="$arg" ;;
            *) ADMIN_USER="$arg" ;;
        esac
    done

    if [ -n "$PLATFORM" ]; then
        echo "=== Exporting $PLATFORM videos to data/exports/ ==="
    else
        echo "=== Exporting all videos to data/exports/ ==="
    fi

    read -s -p "Admin password: " ADMIN_PASS
    echo ""

    local TOKEN
    TOKEN=$(curl -sf --noproxy '*' "http://localhost:$PORT/api/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" \
        | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

    if [ -z "$TOKEN" ]; then
        echo -e "\033[31mLogin failed.\033[0m"
        exit 1
    fi

    local URL="http://localhost:$PORT/api/admin/export"
    [ -n "$PLATFORM" ] && URL="${URL}?platform=${PLATFORM}"

    local RESULT
    RESULT=$(curl -sf --noproxy '*' -X POST "$URL" \
        -H "Authorization: Bearer $TOKEN")
    echo "$RESULT"
    echo -e "\033[32mExport complete. Files in data/exports/\033[0m"
}

import_data() {
    echo "=== Importing video data from data/exports/ ==="
    local PORT=$(get_app_port)
    local TARGET_USER="${1:-}"
    local ADMIN_USER="${2:-admin}"
    local ADMIN_PASS="${3:-}"

    if [ -z "$TARGET_USER" ]; then
        echo "Usage: $0 import <target_username> [admin_user] [admin_pass]"
        echo "  Imports all JSON files from data/exports/ into the specified user's library."
        echo "  Only new videos are imported (existing ones are skipped)."
        exit 1
    fi

    if [ -z "$ADMIN_PASS" ]; then
        read -s -p "Admin password: " ADMIN_PASS
        echo ""
    fi

    local TOKEN
    TOKEN=$(curl -sf --noproxy '*' "http://localhost:$PORT/api/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" \
        | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

    if [ -z "$TOKEN" ]; then
        echo -e "\033[31mLogin failed.\033[0m"
        exit 1
    fi

    local RESULT
    RESULT=$(curl -sf --noproxy '*' -X POST "http://localhost:$PORT/api/admin/import?target_username=$TARGET_USER" \
        -H "Authorization: Bearer $TOKEN")
    echo "$RESULT"
    echo -e "\033[32mImport complete.\033[0m"
}

clean() {
    echo "=== Cleaning build cache ==="
    podman system prune -f
    echo "=== Removing dangling images ==="
    podman image prune -f
    echo "Done."
}

case "$1" in
    init)    init ;;
    start)   start ;;
    stop)    stop ;;
    restart) restart ;;
    rebuild)  rebuild "${@:2}" ;;
    hotpatch) hotpatch "${2:-}" ;;
    export)   export_data "${@:2}" ;;
    import)   import_data "${2:-}" "${3:-admin}" "${4:-}" ;;
    status)   status ;;
    logs)     logs "$@" ;;
    mirror)   mirror "$@" ;;
    clean)    clean ;;
    *)
        echo "Usage: $0 {init|start|stop|restart|rebuild|hotpatch|export|import|status|logs|mirror|clean}"
        echo ""
        echo "Commands:"
        echo "  init                    Create config and data directories"
        echo "  start                   Start all services"
        echo "  stop                    Stop all services"
        echo "  restart                 Restart all services"
        echo "  rebuild [opts] [svc]    Rebuild images and restart (slow, full)"
        echo "    -n, --no-cache          Ignore layer cache"
        echo "    -p, --pull              Re-pull base images"
        echo "    backend|frontend        Rebuild one service only"
        echo "  hotpatch backend|frontend  Patch source code without rebuild (fast, seconds)"
        echo "  export [youtube|bilibili] Export video data to data/exports/ (default: all)"
        echo "  import <user> [admin]   Import videos from data/exports/ into a user's library"
        echo "  status                  Show service status"
        echo "  logs [service]          Follow logs"
        echo "  mirror cn|default       Switch registry mirror"
        echo "  clean                   Remove dangling images and build cache"
        echo ""
        echo "Examples:"
        echo "  $0 hotpatch backend          # code-only change, ~3 seconds"
        echo "  $0 hotpatch frontend         # code-only change, ~10 seconds"
        echo "  $0 rebuild backend           # dependency/Dockerfile changed"
        echo "  $0 rebuild -n backend        # full rebuild, no cache"
        echo ""
        echo "Data sync between machines:"
        echo "  $0 export bilibili           # only export bilibili videos"
        echo ""
        echo "Data sync between machines:"
        echo "  Machine A:  $0 export               # export all videos"
        echo "  Transfer:   rsync -avz data/exports/ data/thumbnails/ user@B:path/data/"
        echo "  Machine B:  $0 import myuser         # import new videos"
        exit 1
        ;;
esac
