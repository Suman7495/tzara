#!/usr/bin/env bash
# Run the working copy of Tzara, server and app, without touching the installed one: `make dev`.
#
#   - builds the macOS app from this checkout
#   - starts a server on its own port (18927) with its own data folder (/tmp/tzara-dev), so the
#     installed server on port 8927 and its token, name and logs are left alone
#   - opens the dev app against it, in the foreground; quit it (menu-bar icon) or press Ctrl+C
#   - on the way out, stops the dev server and brings the installed app back if it was running
#
# The installed app is quit while this runs, because both would answer Control+Option.
# Extra arguments go to the app, e.g. `scripts/dev.sh --snapshot /tmp/box.png`.
# Needs the project's environment: `conda activate tzara && make install` (once).
# Settings: TZARA_DEV_DIR (data folder), TZARA_DEV_PORT, DEV_KEEP_INSTALLED=1 (do not quit the
# installed app).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

dir="${TZARA_DEV_DIR:-/tmp/tzara-dev}"
port="${TZARA_DEV_PORT:-18927}"
export TZARA_DATA_DIR="$dir" TZARA_SERVER_PORT="$port" TZARA_LOG_FILE="$dir/tzara.log"
server_log="$dir/server.out"

fail() { echo "error: $*" >&2; exit 1; }

python -c "import tzara" 2>/dev/null || fail "Tzara is not installed in this Python. Run: conda activate tzara && make install"
if curl -s -o /dev/null "http://127.0.0.1:$port/v1/health"; then
  fail "port $port is already in use (an earlier 'make dev'? run: TZARA_DATA_DIR=$dir TZARA_SERVER_PORT=$port python -m tzara stop)"
fi
mkdir -p "$dir"

echo "==> building the app"
make app >/dev/null
app="app/build/Tzara.app"

# The installed app, if running: remember where it lives, so it can be started again.
installed=""
if [ -z "${DEV_KEEP_INSTALLED:-}" ]; then
  for pid in $(pgrep -f "/Applications/Tzara.app/Contents/MacOS" || true); do
    command_line=$(ps -o command= -p "$pid" 2>/dev/null || true)
    installed="${command_line%%/Contents/MacOS*}"
    [ -n "$installed" ] && kill "$pid" 2>/dev/null && echo "==> quit the installed app (it comes back afterwards)"
  done
fi

server_pid=""
cleanup() {
  trap - EXIT INT TERM
  if [ -n "$server_pid" ]; then
    echo "==> stopping the dev server"
    python -m tzara stop >/dev/null 2>&1 || kill "$server_pid" 2>/dev/null || true
  fi
  if [ -n "$installed" ] && [ -d "$installed" ]; then
    echo "==> reopening the installed app"
    open "$installed" --args --background
  fi
}
trap cleanup EXIT INT TERM

echo "==> starting the dev server on port $port (the model takes about 15 s to load)"
python -m tzara serve >"$server_log" 2>&1 &
server_pid=$!
for _ in $(seq 1 60); do
  curl -s -o /dev/null "http://127.0.0.1:$port/v1/health" && break
  kill -0 "$server_pid" 2>/dev/null || { cat "$server_log" >&2; fail "the dev server did not start"; }
  sleep 0.5
done
curl -s -o /dev/null "http://127.0.0.1:$port/v1/health" || fail "the dev server did not answer; see $server_log"

echo "==> opening the dev app (quit it from the menu-bar icon, or press Ctrl+C, to finish)"
echo "    server log: $dir/tzara.log   app log: ~/Library/Logs/tzara/app.log"
"$app/Contents/MacOS/Tzara" "$@"
