#!/usr/bin/env bash
# Usage: perf/bench/env.sh <label> <checkout_dir> <port>
#
# Build an isolated bench instance of one checkout: seeded throwaway DB,
# fake admin password, own frontend build; then serve it on 127.0.0.1:<port>.
# Never touches the checkout's data/ or config/. Root: /tmp/vsplit-bench/.
set -euo pipefail
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -ne 3 ]]; then
  echo "usage: env.sh <label> <checkout_dir> <port>"
  [[ $# -eq 3 ]] || exit 0
fi
label=$1; src=$(cd "$2" && pwd); port=$3
here=$(cd "$(dirname "$0")" && pwd)
root=/tmp/vsplit-bench/$label
rm -rf "$root"; mkdir -p "$root/config" "$root/data/tmp" "$root/logs"
cat > "$root/config/app.yaml" <<EOF
admin:
  username: admin
  password: bench-only-pass
storage:
  db_path: $root/data/video_split.db
  temp_dir: $root/data/tmp
logging:
  dir: $root/logs
  level: WARNING
EOF
thumb=/tmp/vsplit-bench/thumb.jpg
[[ -f $thumb ]] || ffmpeg -loglevel error -f lavfi -i "nullsrc=s=640x360,geq=random(1)*255:128:128" \
  -frames:v 1 -q:v 4 "$thumb"
(cd "$root" && "$src/backend/.venv/bin/python" "$here/seed.py" "$thumb")
(cd "$src/frontend" && npx vite build --outDir "$root/dist" --emptyOutDir >/dev/null)
cd "$root"
VSLP_FRONTEND_DIST="$root/dist" nohup "$src/backend/.venv/bin/uvicorn" video_split.main:app \
  --host 127.0.0.1 --port "$port" --log-level warning > "$root/logs/uvicorn.out" 2>&1 &
echo $! > "$root/pid"
for _ in $(seq 50); do curl -sf "http://127.0.0.1:$port/health" >/dev/null && break; sleep 0.2; done
echo "$label up on :$port (pid $(cat "$root/pid"))"
