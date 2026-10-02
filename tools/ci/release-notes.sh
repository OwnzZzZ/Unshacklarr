#!/bin/sh
# A release's notes: how to pull its image, then its section of CHANGELOG.md (from "## [1.0.0]" to the next "## [").
#   sh tools/ci/release-notes.sh <version> <image> <changelog URL>
set -eu
version=$1 image=$2 changelog=$3
section=$(awk -v v="## [$version]" 'index($0, v) == 1 { on = 1; next } on && /^## \[/ { exit } on && !/^\[[^]]+\]: / { print }' CHANGELOG.md)
[ -n "$section" ] || { echo "CHANGELOG.md has no section for $version" >&2; exit 1; }
printf '```bash\ndocker pull %s:%s\n```\n%s\n\nEvery version: [CHANGELOG.md](%s).\n' "$image" "$version" "$section" "$changelog"
