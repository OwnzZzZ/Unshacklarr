#!/bin/sh
# The demo as static files in demo/site: the page as it is, demo.js in front of it, data.json recorded
# against a made-up Sonarr and Unshackle. Any static host serves it, at its root or in a folder.
#   sh demo/build.sh && python3 -m http.server -d demo/site 8800
set -e
cd "$(dirname "$0")/.."
rm -rf demo/site
mkdir -p demo/site
cp -R unshacklarr/static/. demo/site/
cp demo/demo.js demo/site/
uv run python demo/record.py demo/site/data.json
# its files by relative paths (a host may serve it in a folder), and demo.js before the page's own script
sed -i.bak -E \
  -e 's#(["`])/(apple-touch-icon|manifest|xterm|codemirror|unshackle-keys|icon-|i18n/)#\1\2#g' \
  -e 's#<head>#<head>\n<script src="demo.js"></script>#' \
  demo/site/index.html
rm demo/site/index.html.bak demo/site/sw.js
echo "demo/site is ready"
