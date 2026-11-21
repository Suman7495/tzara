#!/usr/bin/env bash
# Builds build/Tzara.app: the macOS app as a proper application bundle.
#
#   scripts/build_app.sh                          # ad-hoc signature
#   TZARA_SIGN_IDENTITY="Tzara Dev" scripts/build_app.sh
#
# macOS ties the Input Monitoring permission to the app's code signature. An ad-hoc
# signature changes on every build, so you would be asked again each time. A stable
# identity (see the README) keeps the permission across rebuilds.
set -euo pipefail

cd "$(dirname "$0")/.."
CONFIGURATION="${CONFIGURATION:-release}"
IDENTITY="${TZARA_SIGN_IDENTITY:--}"
APP="build/Tzara.app"

swift build --configuration "$CONFIGURATION" --product TzaraApp
BINARY="$(swift build --configuration "$CONFIGURATION" --show-bin-path)/TzaraApp"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$BINARY" "$APP/Contents/MacOS/Tzara"
cp Resources/Info.plist "$APP/Contents/Info.plist"

# The bundle must contain the executable its Info.plist names, or macOS cannot launch it.
EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
if [[ ! -x "$APP/Contents/MacOS/$EXECUTABLE" ]]; then
    echo "error: Info.plist names '$EXECUTABLE' but $APP/Contents/MacOS has: $(ls "$APP/Contents/MacOS")" >&2
    exit 1
fi

codesign --force --sign "$IDENTITY" --identifier ai.telekinesis.tzara.app "$APP"
if [[ "$IDENTITY" == "-" ]]; then
    echo "note: signed ad-hoc, so macOS will ask for Input Monitoring again after each rebuild."
fi
echo "built $APP"
