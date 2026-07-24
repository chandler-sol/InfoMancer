#!/usr/bin/env sh
set -eu
command -v docker >/dev/null 2>&1 || {
  echo "Docker is not installed or is not available in this terminal. Run this reset on the machine that hosts InfoMancer, or install Docker first." >&2
  exit 1
}

MODE="${1:-blank}"
case "$MODE" in blank|sample) ;; *) echo "Use: $0 [blank|sample]" >&2; exit 2 ;; esac

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WORKSPACE=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
test -f "$WORKSPACE/compose.sandbox.yaml" && test -f "$WORKSPACE/Dockerfile" || {
  echo "The sandbox reset must run from the InfoMancer repository." >&2; exit 1;
}
cd "$WORKSPACE"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  echo "Python 3 is required to create the sandbox media fixtures. Install Python 3 on the InfoMancer host and try again." >&2
  exit 1
fi

if [ -d "$WORKSPACE/data-sandbox" ]; then
  docker compose -p infomancer-sandbox -f compose.sandbox.yaml run --rm --no-deps \
    --entrypoint sh infomancer -c \
    'find /app/data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'
fi
docker compose -p infomancer-sandbox -f compose.sandbox.yaml down --remove-orphans
rm -rf -- "$WORKSPACE/data-sandbox" "$WORKSPACE/sandbox-media"
if [ ! -f .env.sandbox ]; then
  cp .env.sandbox.example .env.sandbox
  SECRET=$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(48))')
  sed -i "s/replace-with-a-random-sandbox-value/$SECRET/" .env.sandbox
fi
$PYTHON_BIN scripts/create_sandbox_media.py sandbox-media
docker compose -p infomancer-sandbox -f compose.sandbox.yaml up -d --build
if [ "$MODE" = "sample" ]; then
  docker compose -p infomancer-sandbox -f compose.sandbox.yaml exec -T infomancer python -m app.sandbox_seed
fi
echo "InfoMancer sandbox ($MODE) is ready at http://127.0.0.1:8788"
[ "$MODE" = "sample" ] && echo "Sign in with: sandbox / sandbox librarian password" || true
