#!/usr/bin/env bash
# Creates the certificate that signs Tzara, once, so that macOS keeps the Input Monitoring
# permission across rebuilds and releases (it ties the permission to the signature).
#
#   scripts/create_signing_identity.sh
#
# It does two things:
#   1. Puts the certificate in your login keychain, so `make app` signs with it from now on.
#   2. Copies the same certificate, base64 encoded, to the clipboard and prints its password.
#      Add them as GitHub secrets (repo Settings > Secrets and variables > Actions):
#          TZARA_SIGNING_P12        the clipboard
#          TZARA_SIGNING_PASSWORD   the printed password
#      so that releases are signed with the very same certificate.
#
# Run it ONCE. A different certificate means a different signature, which brings back the
# permission prompt. Keep build/signing/ (the certificate file) somewhere safe, or delete it
# once the secrets are saved; the keychain and GitHub keep copies.
#
# Environment: TZARA_SIGN_IDENTITY (the certificate's name), TZARA_KEYCHAIN (default: login),
# TZARA_NO_CLIPBOARD (do not touch the clipboard).
set -euo pipefail

cd "$(dirname "$0")/.."
NAME="${TZARA_SIGN_IDENTITY:-Tzara Signing}"
KEYCHAIN="${TZARA_KEYCHAIN:-$HOME/Library/Keychains/login.keychain-db}"
OUT="build/signing"
# The system's openssl writes the certificate format macOS can import; newer builds do not.
OPENSSL=/usr/bin/openssl

if security find-identity -p codesigning "$KEYCHAIN" | grep -q "\"$NAME\""; then
    echo "A certificate named \"$NAME\" is already in the keychain, so nothing was created." >&2
    echo "Keep using it. To start over, delete it in Keychain Access first (and expect one more" >&2
    echo "permission prompt, since the signature changes)." >&2
    exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT"
chmod 700 "$OUT"
PASSWORD="$("$OPENSSL" rand -hex 16)"

cat >"$OUT/openssl.cnf" <<CONF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CONF

"$OPENSSL" req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$OUT/openssl.cnf" \
    -keyout "$OUT/key.pem" -out "$OUT/cert.pem" 2>/dev/null
"$OPENSSL" pkcs12 -export -inkey "$OUT/key.pem" -in "$OUT/cert.pem" -name "$NAME" \
    -out "$OUT/tzara-signing.p12" -passout "pass:$PASSWORD"
rm -f "$OUT/key.pem" "$OUT/cert.pem" "$OUT/openssl.cnf"

security import "$OUT/tzara-signing.p12" -k "$KEYCHAIN" -P "$PASSWORD" -T /usr/bin/codesign >/dev/null
base64 <"$OUT/tzara-signing.p12" | tr -d '\n' >"$OUT/tzara-signing.p12.base64"
if [[ -z "${TZARA_NO_CLIPBOARD:-}" ]]; then pbcopy <"$OUT/tzara-signing.p12.base64"; fi

echo "Created \"$NAME\" in your keychain; \`make app\` now signs with it."
echo
echo "For releases, add two GitHub secrets (Settings > Secrets and variables > Actions):"
echo "  TZARA_SIGNING_P12        paste the clipboard (it is also in $OUT/tzara-signing.p12.base64)"
echo "  TZARA_SIGNING_PASSWORD   $PASSWORD"
echo
echo "Then delete $OUT. Never commit it."
