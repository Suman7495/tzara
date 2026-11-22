#!/usr/bin/env bash
# Print the version after LATEST for a bump LEVEL (patch, minor or major).
#   scripts/next_version.sh 0.1.2 patch   ->  0.1.3
#   scripts/next_version.sh 0.1.9 minor   ->  0.2.0
set -euo pipefail

latest="${1:-}"
level="${2:-patch}"
[[ "$latest" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || { echo "error: not a version: '$latest'" >&2; exit 1; }
major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"; patch="${BASH_REMATCH[3]}"

case "$level" in
  patch) patch=$((patch + 1)) ;;
  minor) minor=$((minor + 1)); patch=0 ;;
  major) major=$((major + 1)); minor=0; patch=0 ;;
  *) echo "error: level must be patch, minor or major, not '$level'" >&2; exit 1 ;;
esac
echo "$major.$minor.$patch"
