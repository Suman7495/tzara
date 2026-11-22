#!/usr/bin/env bash
# Write VERSION into pyproject.toml. The release workflow runs this on main, so nobody has to.
#   scripts/set_version.sh 0.1.3
set -euo pipefail

version="${1:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "error: give a version like 0.1.3 (scripts/set_version.sh 0.1.3)" >&2; exit 1; }
cd "$(git rev-parse --show-toplevel)"

current=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
newest=$(printf '%s\n%s\n' "$current" "$version" | sort -V | tail -n 1)
if [ "$version" = "$current" ] || [ "$newest" != "$version" ]; then
  echo "error: $version is not newer than the current $current" >&2
  exit 1
fi

python3 - "$version" <<'PY'
import re, sys
text = open("pyproject.toml").read()
text, count = re.subn(r'(?m)^version = "[^"]*"', f'version = "{sys.argv[1]}"', text, count=1)
assert count == 1, "version line not found"
open("pyproject.toml", "w").write(text)
PY
echo "Version $current -> $version"
