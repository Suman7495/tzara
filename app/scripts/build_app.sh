#!/usr/bin/env bash
# Builds build/Tzara.app: the macOS app as a proper application bundle.
#
#   scripts/build_app.sh
#
# macOS ties the Input Monitoring permission to the app's code signature. An ad-hoc
# signature changes on every build, so you would be asked again each time. A stable
# certificate keeps the permission across rebuilds and updates: create it once with
# scripts/create_signing_identity.sh. This script signs with "Tzara Signing" when it finds it
# (or the certificate named in TZARA_SIGN_IDENTITY), and ad-hoc otherwise.
# TZARA_REQUIRE_SIGNING=1 turns the ad-hoc fallback into an error; releases use it.
set -euo pipefail

cd "$(dirname "$0")/.."
CONFIGURATION="${CONFIGURATION:-release}"
APP="build/Tzara.app"
IDENTITY="${TZARA_SIGN_IDENTITY:-Tzara Signing}"
if ! security find-identity -p codesigning | grep -q "\"$IDENTITY\""; then
    if [[ -n "${TZARA_REQUIRE_SIGNING:-}" ]]; then
        echo "error: no signing certificate \"$IDENTITY\" found, and TZARA_REQUIRE_SIGNING is set" >&2
        exit 1
    fi
    IDENTITY="-"
fi
# The app reports the version of the Python package it ships with.
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' ../pyproject.toml | head -1)"
if [[ -z "$VERSION" ]]; then
    echo "error: cannot read the version from ../pyproject.toml" >&2
    exit 1
fi

swift build --configuration "$CONFIGURATION" --product TzaraApp
BINARY="$(swift build --configuration "$CONFIGURATION" --show-bin-path)/TzaraApp"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$BINARY" "$APP/Contents/MacOS/Tzara"
cp Resources/Info.plist "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" -c "Set :CFBundleVersion $VERSION" \
    "$APP/Contents/Info.plist"

# The bundle must contain the executable its Info.plist names, or macOS cannot launch it.
EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
if [[ ! -x "$APP/Contents/MacOS/$EXECUTABLE" ]]; then
    echo "error: Info.plist names '$EXECUTABLE' but $APP/Contents/MacOS has: $(ls "$APP/Contents/MacOS")" >&2
    exit 1
fi

codesign --force --sign "$IDENTITY" --identifier ai.telekinesis.tzara.app "$APP"
if [[ "$IDENTITY" == "-" ]]; then
    echo "note: signed ad-hoc, so macOS will ask for Input Monitoring again after each rebuild."
    echo "      Run scripts/create_signing_identity.sh once to stop that."
fi
echo "built $APP $VERSION, signed with: $(codesign -dr - "$APP" 2>&1 | sed -n 's/^designated => //p')"
