#!/usr/bin/env bash
# Usage: pages.sh <tag> <originA> [originB]  — bench the 3 core pages (10 runs, fast4g)
set -euo pipefail
cd "$(dirname "$0")"
tag=$1; a=$2; b=${3:-}
R="uv run -q --with playwright==1.64.1 python run.py"
bench() {  # name path ready
  local extra=()
  [[ -n $b ]] && extra=(--url-b "$b$2")
  $R --url "$a$2" ${extra[@]+"${extra[@]}"} --ready "$3" --runs 10 --profile fast4g --out "$tag-$1.json" 2>/dev/null | tail -3
}
bench detail /video/1 "document.body.innerText.includes('片段 19')"
bench library /library "(document.body.innerText.match(/测速视频 \\d+：/g)||[]).length >= 20"
bench apidocs /api-docs "document.body.innerText.includes('/api/videos/analyze')"
