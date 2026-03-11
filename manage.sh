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

    BUILD_CMD="REGISTRY=$REGISTRY podman-compose -f $COMPOSE_FILE build --build-arg REGISTRY=$REGISTRY"
    [ -n "$NO_CACHE" ] && BUILD_CMD="$BUILD_CMD --no-cache"
    [ -n "$PULL" ]     && BUILD_CMD="$BUILD_CMD $PULL"
    [ -n "$SERVICE" ]  && BUILD_CMD="$BUILD_CMD $SERVICE"

    eval $BUILD_CMD

    if [ $? -ne 0 ]; then
        echo -e "\033[31mBuild failed!\033[0m"
        exit 1
    fi

    echo ""
    echo "=== Restarting services ==="

    # Always full down+up so containers pick up the newly built image.
    # podman-compose single-service rm sometimes keeps stale containers.
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
    if curl -sf "http://localhost:$PORT/api/health" > /dev/null 2>&1; then
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
    rebuild) rebuild "${@:2}" ;;
    status)  status ;;
    logs)    logs "$@" ;;
    mirror)  mirror "$@" ;;
    clean)   clean ;;
    *)
        echo "Usage: $0 {init|start|stop|restart|rebuild|status|logs|mirror|clean}"
        echo ""
        echo "Commands:"
        echo "  init                  Create config and data directories"
        echo "  start                 Start all services"
        echo "  stop                  Stop all services"
        echo "  restart               Restart all services"
        echo "  rebuild [opts] [svc]  Rebuild images and restart"
        echo "    -n, --no-cache        Ignore layer cache"
        echo "    -p, --pull            Re-pull base images"
        echo "    backend|frontend      Rebuild one service only"
        echo "  status                Show service status"
        echo "  logs [service]        Follow logs"
        echo "  mirror cn|default     Switch registry mirror"
        echo "  clean                 Remove dangling images and build cache"
        echo ""
        echo "Examples:"
        echo "  $0 rebuild                   # incremental build (fastest)"
        echo "  $0 rebuild backend           # rebuild backend only"
        echo "  $0 rebuild -n backend        # full rebuild backend, no cache"
        echo "  $0 rebuild -p                # rebuild + re-pull base images"
        exit 1
        ;;
esac
