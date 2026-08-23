#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool_dir="$repo_root/tools/showcase"
url="${INFOMANCER_SHOWCASE_URL:-http://127.0.0.1:8787}"
username="${INFOMANCER_SHOWCASE_USERNAME:-}"
output="${INFOMANCER_SHOWCASE_OUTPUT:-showcase/screenshots}"
variants="${INFOMANCER_SHOWCASE_VARIANTS:-desktop,social,mobile}"
only="${INFOMANCER_SHOWCASE_ONLY:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) url="$2"; shift 2 ;;
    --username) username="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    --variants) variants="$2"; shift 2 ;;
    --only) only="$2"; shift 2 ;;
    --headed) export INFOMANCER_SHOWCASE_HEADLESS=0; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

command -v node >/dev/null 2>&1 || { echo "Node.js is required." >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is required." >&2; exit 1; }

export INFOMANCER_SHOWCASE_URL="$url"
export INFOMANCER_SHOWCASE_OUTPUT="$output"
export INFOMANCER_SHOWCASE_VARIANTS="$variants"
[[ -n "$only" ]] && export INFOMANCER_SHOWCASE_ONLY="$only"

password_was_set=0
if [[ -n "$username" ]]; then
  export INFOMANCER_SHOWCASE_USERNAME="$username"
  if [[ -z "${INFOMANCER_SHOWCASE_PASSWORD:-}" ]]; then
    read -r -s -p "InfoMancer password for $username: " INFOMANCER_SHOWCASE_PASSWORD
    echo
    export INFOMANCER_SHOWCASE_PASSWORD
    password_was_set=1
  fi
fi

cd "$tool_dir"
if [[ ! -d node_modules/playwright ]]; then
  echo "Installing screenshot tooling..."
  npm install
fi

echo "Ensuring Chromium for Playwright is installed..."
npx playwright install chromium
npm run capture

if [[ "$password_was_set" -eq 1 ]]; then
  unset INFOMANCER_SHOWCASE_PASSWORD
fi
