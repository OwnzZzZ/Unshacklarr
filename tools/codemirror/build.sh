#!/bin/sh
# Rebuild unshacklarr/static/codemirror.js from the versions package-lock.json pins (all of them, not only package.json's).
# Bump the ?v= of its import in index.html after a rebuild (the file is cached a day).
set -e
cd "$(dirname "$0")"
npm ci --no-audit --no-fund
npx esbuild entry.js --bundle --minify --format=esm --outfile=bundle.js
cat header.txt bundle.js > ../../unshacklarr/static/codemirror.js
rm bundle.js
