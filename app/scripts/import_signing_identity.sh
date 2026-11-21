#!/usr/bin/env bash
# Puts the signing certificate into a temporary keychain, for the release build on CI.
#
# Reads TZARA_SIGNING_P12 (the certificate, base64) and TZARA_SIGNING_PASSWORD. When run by
# GitHub Actions it also exports TZARA_SIGN_IDENTITY for the next steps. Without the secrets
# it does nothing, and the build is then signed ad-hoc (which a release refuses to accept).
set -euo pipefail

NAME="${TZARA_SIGN_IDENTITY:-Tzara Signing}"
if [[ -z "${TZARA_SIGNING_P12:-}" || -z "${TZARA_SIGNING_PASSWORD:-}" ]]; then
    echo "no signing certificate provided" >&2
    exit 0
fi

WORK="${RUNNER_TEMP:-$(mktemp -d)}"
KEYCHAIN="${TZARA_KEYCHAIN:-$WORK/tzara-signing.keychain-db}"
KEYCHAIN_PASSWORD="$(uuidgen)"

security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security set-keychain-settings -lut 21600 "$KEYCHAIN"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
echo "$TZARA_SIGNING_P12" | base64 --decode >"$WORK/tzara-signing.p12"
security import "$WORK/tzara-signing.p12" -k "$KEYCHAIN" -P "$TZARA_SIGNING_PASSWORD" -T /usr/bin/codesign >/dev/null
security set-key-partition-list -S apple-tool:,apple: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN" >/dev/null
rm -f "$WORK/tzara-signing.p12"

# codesign only looks in keychains on the search list. Read it line by line: paths may have spaces.
EXISTING=()
while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line#\"}"
    EXISTING+=("${line%\"}")
done < <(security list-keychains -d user)
security list-keychains -d user -s "$KEYCHAIN" "${EXISTING[@]}"

echo "imported \"$NAME\" into $KEYCHAIN"
if [[ -n "${GITHUB_ENV:-}" ]]; then
    echo "TZARA_SIGN_IDENTITY=$NAME" >>"$GITHUB_ENV"
fi
