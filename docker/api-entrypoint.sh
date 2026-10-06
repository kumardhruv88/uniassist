#!/bin/sh
# Entrypoint of the UniAssist API image (docker/api.Dockerfile). Runs the given command as the unprivileged
# user "app" (uid 10001) after two container-specific fixes:
#
# 1. OLLAMA_BASE_URL=http://localhost:11434 (the value in .env.example, picked up by compose from a local .env)
#    would point at the container itself. When host.docker.internal resolves, it is used instead.
# 2. The runtime folder (SQLite, Chroma, uploaded files) is often a bind-mounted host folder. On Linux it can be
#    owned by root (Docker created it) or by the host user, so uid 10001 cannot write to it; it is then handed
#    to "app" once. Docker Desktop (macOS, Windows) mounts are already writable and are left untouched.
set -eu

case "${OLLAMA_BASE_URL:-}" in
  http://localhost:* | http://localhost | http://127.0.0.1:* | http://127.0.0.1)
    if getent hosts host.docker.internal >/dev/null 2>&1; then
      fixed=$(printf '%s' "$OLLAMA_BASE_URL" | sed -E 's#^http://(localhost|127\.0\.0\.1)#http://host.docker.internal#')
      echo "entrypoint: OLLAMA_BASE_URL=$OLLAMA_BASE_URL is the container itself; using $fixed" >&2
      export OLLAMA_BASE_URL="$fixed"
    fi
    ;;
esac

if [ "$(id -u)" = "0" ]; then
  runtime_dir="${RUNTIME_DIR:-/app/data/runtime}"
  mkdir -p "$runtime_dir"
  if ! setpriv --reuid=app --regid=app --init-groups test -w "$runtime_dir"; then
    echo "entrypoint: $runtime_dir is not writable for user app (uid $(id -u app)); changing its owner" >&2
    chown -R app:app "$runtime_dir" \
      || echo "entrypoint: chown failed; make $runtime_dir writable for uid $(id -u app)" >&2
  fi
  export HOME=/home/app
  exec setpriv --reuid=app --regid=app --init-groups -- "$@"
fi
exec "$@"
