#!/bin/sh
# Tzara installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Suman7495/tzara/main/install.sh | sh
#
# Or read it first, then run it:
#
#   curl -fsSLO https://raw.githubusercontent.com/Suman7495/tzara/main/install.sh
#   sh install.sh
#
# What it does, with no sudo:
#   1. Installs uv (https://docs.astral.sh/uv/) if you don't have it. uv brings its own Python.
#   2. Installs Tzara with uv.
#   3. Runs `tzara setup`: Ollama, the model, the server at login, and the macOS app.
#
# Anything you add is passed to `tzara setup`:   sh install.sh --no-app
# To remove everything it added:                 sh install.sh --uninstall
#
# Environment:
#   TZARA_REF=v0.1.0      install this git tag or branch instead of main
#   TZARA_SOURCE=PATH     install from this path or URL instead of GitHub
#   TZARA_DRY_RUN=1       print what would run, and do nothing
#
# Everything is inside main() so that a download cut short cannot run half a script.

set -eu

REPO="Suman7495/tzara"
UV_INSTALLER="https://astral.sh/uv/install.sh"

say() {
    printf '%s\n' "$*"
}

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

# Runs a command, or only shows it when TZARA_DRY_RUN is set.
run() {
    if [ -n "${TZARA_DRY_RUN:-}" ]; then
        say "+ $*"
    else
        "$@"
    fi
}

check_system() {
    case "$(uname -s)" in
        Darwin | Linux) ;;
        *) fail "Tzara supports macOS and Linux. On Windows, use WSL." ;;
    esac
    command -v curl >/dev/null 2>&1 || fail "curl is required."
}

ensure_uv() {
    if command -v uv >/dev/null 2>&1; then
        return
    fi
    say "Installing uv, a Python installer (it brings its own Python)..."
    if [ -n "${TZARA_DRY_RUN:-}" ]; then
        say "+ curl -LsSf $UV_INSTALLER | sh"
        return
    fi
    curl -LsSf "$UV_INSTALLER" | sh
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    command -v uv >/dev/null 2>&1 || fail "uv installed, but is not on PATH. Open a new terminal and run this again."
}

# Where `uv tool install` puts commands.
tool_bin() {
    if [ -n "${TZARA_DRY_RUN:-}" ] && ! command -v uv >/dev/null 2>&1; then
        printf '%s' "<uv tool bin>"
    else
        uv tool dir --bin
    fi
}

main() {
    uninstall=0
    if [ "${1:-}" = "--uninstall" ]; then
        uninstall=1
        shift
    fi

    check_system
    ensure_uv
    bin="$(tool_bin)"

    if [ "$uninstall" = 1 ]; then
        if [ -x "$bin/tzara" ] || [ -n "${TZARA_DRY_RUN:-}" ]; then
            run "$bin/tzara" uninstall
        fi
        run uv tool uninstall tzara || true
        say "Tzara is removed. Ollama and its models were left in place."
        return
    fi

    source="${TZARA_SOURCE:-git+https://github.com/$REPO@${TZARA_REF:-main}}"
    say "Installing Tzara from $source"
    run uv tool install --force "$source"

    run "$bin/tzara" setup "$@"

    case ":$PATH:" in
        *":$bin:"*) ;;
        *) say "To use the 'tzara' command, add $bin to your PATH (uv tool update-shell does it for you)." ;;
    esac
}

main "$@"
