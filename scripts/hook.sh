#!/usr/bin/env bash
# Run a project tool (pylint, mypy, pytest) from a git hook, wherever git was started.
#
# Git started from a terminal with the project's environment active finds the tools on PATH.
# Git started by an editor's push button does not, and the hook then fails with
# "Executable `pytest` not found". So look in the usual places for the environment too:
#   $TZARA_ENV_BIN (a bin directory), ./.venv/bin, the active conda environment, and a conda
#   environment named tzara under the common conda install locations.
set -euo pipefail

tool="${1:?usage: hook.sh TOOL [ARGS...]}"

candidates=()
[ -n "${TZARA_ENV_BIN:-}" ] && candidates+=("$TZARA_ENV_BIN")
candidates+=("$PWD/.venv/bin")
[ -n "${CONDA_PREFIX:-}" ] && candidates+=("$CONDA_PREFIX/bin")
for root in miniconda3 anaconda3 miniforge3 mambaforge micromamba; do
  candidates+=("$HOME/$root/envs/tzara/bin" "$HOME/.local/share/$root/envs/tzara/bin")
done
candidates+=("/opt/homebrew/Caskroom/miniforge/base/envs/tzara/bin" "/opt/miniconda3/envs/tzara/bin")

if ! command -v "$tool" >/dev/null 2>&1; then
  for dir in "${candidates[@]}"; do
    if [ -x "$dir/$tool" ]; then
      PATH="$dir:$PATH"
      break
    fi
  done
fi

if ! command -v "$tool" >/dev/null 2>&1; then
  echo "hook: '$tool' not found. Activate the project's environment, or set TZARA_ENV_BIN to its bin directory." >&2
  exit 1
fi
exec "$@"
