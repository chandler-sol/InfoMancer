#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
cd "$SCRIPT_DIR" || exit 1

MIN_DOCKER_ENGINE="24.0.0"
MIN_DOCKER_COMPOSE="2.20.0"
DOCKER_MAC_URL="https://docs.docker.com/desktop/setup/install/mac-install/"
DOCKER_LINUX_URL="https://docs.docker.com/engine/install/"

say() {
  printf '%s\n' "$*"
}

fail() {
  printf '\nInfoMancer Server setup stopped: %s\n' "$*" >&2
  exit 1
}

dc() {
  docker compose -f compose.yaml -f compose.media.yaml "$@"
}

version_at_least() {
  actual=$1
  minimum=$2
  awk -v actual="$actual" -v minimum="$minimum" '
    BEGIN {
      sub(/^[^0-9]*/, "", actual)
      split(actual, a, ".")
      split(minimum, m, ".")
      for (i = 1; i <= 3; i++) {
        av = a[i] + 0
        mv = m[i] + 0
        if (av > mv) exit 0
        if (av < mv) exit 1
      }
      exit 0
    }
  '
}

open_url() {
  url=$1
  if [ "$PLATFORM" = "macos" ] && command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
    return
  fi
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
    return
  fi
  if command -v gio >/dev/null 2>&1; then
    gio open "$url" >/dev/null 2>&1 || true
  fi
}

docker_help() {
  problem=$1
  if [ "$PLATFORM" = "macos" ]; then
    url=$DOCKER_MAC_URL
  else
    url=$DOCKER_LINUX_URL
  fi

  say ""
  say "Docker needs attention"
  say "----------------------"
  say "$problem"
  say ""
  say "InfoMancer Server requires:"
  say "  Docker Engine 24.0 or newer"
  say "  Docker Compose 2.20 or newer"
  say ""
  say "Official Docker instructions:"
  say "  $url"
  say ""
  printf 'Open the official Docker instructions now? [Y/n]: '
  IFS= read -r answer || exit 1
  case "$answer" in
    n|N|no|NO|No) ;;
    *) open_url "$url" ;;
  esac
  fail "Install or update Docker, make sure it is running, then run this helper again."
}

replace_env_value() {
  key=$1
  value=$2
  tmp=".env.infomancer.tmp.$$"
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    index($0, key "=") == 1 { print key "=" value; found = 1; next }
    { print }
    END { if (!found) print key "=" value }
  ' .env > "$tmp" || return 1
  mv "$tmp" .env
}

yaml_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

prompt_path() {
  label=$1
  current=""
  while :; do
    printf '%s folder (leave blank if you do not have one): ' "$label"
    IFS= read -r current || exit 1
    case "$current" in
      "~/"*) current="$HOME/${current#\~/}" ;;
    esac
    if [ -z "$current" ] || [ -d "$current" ]; then
      PROMPT_PATH=$current
      return 0
    fi
    printf 'That folder was not found: %s\nUse it anyway? [y/N]: ' "$current"
    IFS= read -r answer || exit 1
    case "$answer" in
      y|Y|yes|YES|Yes) PROMPT_PATH=$current; return 0 ;;
    esac
  done
}

case "$(uname -s 2>/dev/null || printf unknown)" in
  Darwin) PLATFORM=macos ;;
  Linux) PLATFORM=linux ;;
  *) fail "This helper supports macOS and Linux. Windows users should run Setup-InfoMancer.cmd." ;;
esac

say ""
say "InfoMancer Server Setup"
say "======================="
say "This helper checks Docker, creates the local config files, connects your media folders,"
say "starts InfoMancer, and prints the address and one-time setup code."
say ""
say "Checking Docker..."

command -v docker >/dev/null 2>&1 || docker_help "Docker was not found on this computer."

COMPOSE_VERSION=$(docker compose version --short 2>/dev/null | head -n 1 || true)
if [ -z "$COMPOSE_VERSION" ]; then
  COMPOSE_VERSION=$(docker compose version 2>/dev/null | awk '{print $NF}' | head -n 1 || true)
fi
[ -n "$COMPOSE_VERSION" ] || docker_help "Docker is installed, but the Docker Compose plugin was not found."
version_at_least "$COMPOSE_VERSION" "$MIN_DOCKER_COMPOSE" || docker_help "Docker Compose $COMPOSE_VERSION was found, but InfoMancer requires 2.20 or newer."
say "  Docker Compose $COMPOSE_VERSION ... OK"

if ! docker info >/dev/null 2>&1; then
  if [ "$PLATFORM" = "macos" ] && [ -d "/Applications/Docker.app" ]; then
    printf 'Docker Desktop is installed but is not running. Start it now? [Y/n]: '
    IFS= read -r answer || exit 1
    case "$answer" in
      n|N|no|NO|No) ;;
      *)
        open -a Docker >/dev/null 2>&1 || true
        say "Waiting for Docker Desktop to start..."
        count=0
        while [ "$count" -lt 60 ]; do
          if docker info >/dev/null 2>&1; then
            break
          fi
          sleep 2
          count=$((count + 1))
        done
        ;;
    esac
  fi
fi

if ! docker info >/dev/null 2>&1; then
  if [ "$PLATFORM" = "linux" ]; then
    docker_help "Docker is installed, but the Docker engine is not reachable. Start the Docker service and make sure your user has permission to use Docker."
  fi
  docker_help "Docker Desktop is installed, but its engine is not running. Start Docker Desktop and wait until it reports that Docker is ready."
fi

ENGINE_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null | head -n 1 || true)
[ -n "$ENGINE_VERSION" ] || docker_help "Docker is running, but InfoMancer could not read the Docker Engine version."
version_at_least "$ENGINE_VERSION" "$MIN_DOCKER_ENGINE" || docker_help "Docker Engine $ENGINE_VERSION was found, but InfoMancer requires 24.0 or newer."
say "  Docker Engine $ENGINE_VERSION ... OK"
say "  Docker engine is running ... OK"
say ""

if [ ! -f .env ]; then
  cp .env.example .env || fail "Could not create .env from .env.example."
  say "Created .env"
else
  say "Keeping existing .env"
fi

mkdir -p data || fail "Could not create the data folder."

if [ "$PLATFORM" = "linux" ]; then
  replace_env_value INFOMANCER_UID "$(id -u)" || fail "Could not set the Linux user ID in .env."
  replace_env_value INFOMANCER_GID "$(id -g)" || fail "Could not set the Linux group ID in .env."
  say "Set Linux file ownership to UID $(id -u) / GID $(id -g)"
fi

REUSE_MEDIA=no
if [ -f compose.media.yaml ]; then
  printf 'An existing compose.media.yaml was found. Keep it? [Y/n]: '
  IFS= read -r answer || exit 1
  case "$answer" in
    n|N|no|NO|No) REUSE_MEDIA=no ;;
    *) REUSE_MEDIA=yes ;;
  esac
fi

if [ "$REUSE_MEDIA" != "yes" ]; then
  while :; do
    prompt_path "Movies"
    MOVIES=$PROMPT_PATH
    prompt_path "TV Shows"
    TV=$PROMPT_PATH
    if [ -n "$MOVIES" ] || [ -n "$TV" ]; then
      break
    fi
    say "Enter at least one Movies or TV Shows folder. You can add more folders later."
  done

  {
    say "services:"
    say "  infomancer:"
    say "    volumes:"
    if [ -n "$MOVIES" ]; then
      say "      - type: bind"
      printf "        source: '%s'\n" "$(yaml_escape "$MOVIES")"
      say "        target: /media/movies"
    fi
    if [ -n "$TV" ]; then
      say "      - type: bind"
      printf "        source: '%s'\n" "$(yaml_escape "$TV")"
      say "        target: /media/tv"
    fi
  } > compose.media.yaml || fail "Could not create compose.media.yaml."
  say "Created compose.media.yaml"
fi

say ""
printf 'Start InfoMancer Server now? [Y/n]: '
IFS= read -r answer || exit 1
case "$answer" in
  n|N|no|NO|No)
    say "Setup files are ready. Start later with:"
    say "docker compose -f compose.yaml -f compose.media.yaml up -d --build"
    exit 0
    ;;
esac

say ""
say "Building and starting InfoMancer. The first build can take a few minutes..."
dc up -d --build || fail "Docker could not build/start InfoMancer. Run the troubleshooting command shown in START-HERE.txt."

container_id=$(dc ps -q infomancer 2>/dev/null | head -n 1)
[ -n "$container_id" ] || fail "The InfoMancer container did not start."

say "Waiting for InfoMancer to become ready..."
count=0
health=""
while [ "$count" -lt 60 ]; do
  health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)
  if [ "$health" = "healthy" ]; then
    break
  fi
  if [ "$health" = "unhealthy" ] || [ "$health" = "exited" ] || [ "$health" = "dead" ]; then
    dc logs --tail=80 infomancer || true
    fail "InfoMancer stopped before it became ready."
  fi
  sleep 2
  count=$((count + 1))
done

[ "$health" = "healthy" ] || fail "InfoMancer did not become healthy within two minutes. Check: docker compose -f compose.yaml -f compose.media.yaml logs --tail=200 infomancer"

# Visiting /setup creates the protected one-time bootstrap token on a brand-new server.
dc exec -T infomancer python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8787/setup", timeout=5).read()' >/dev/null 2>&1 || true
sleep 1

TOKEN=""
if [ -f data/bootstrap-token ]; then
  TOKEN=$(cat data/bootstrap-token 2>/dev/null || true)
fi

LAN_IP=""
if [ "$PLATFORM" = "linux" ]; then
  LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
else
  DEFAULT_IF=$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}' || true)
  if [ -n "$DEFAULT_IF" ]; then
    LAN_IP=$(ipconfig getifaddr "$DEFAULT_IF" 2>/dev/null || true)
  fi
fi

say ""
say "InfoMancer Server is ready."
say ""
say "On this computer:"
say "  http://127.0.0.1:8787"
if [ -n "$LAN_IP" ]; then
  say ""
  say "From another computer on this network:"
  say "  http://$LAN_IP:8787"
fi

if [ -n "$TOKEN" ]; then
  say ""
  say "One-time setup code:"
  say "  $TOKEN"
  say ""
  say "Copy that code into the first Librarian setup screen."
else
  say ""
  say "No one-time setup code was found. If you already created a Librarian account, that is expected."
fi

say ""
say "Do not port-forward port 8787 to the public Internet."
say "Setup complete."
