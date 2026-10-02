#!/bin/sh
# Copy xterm.js (MIT) and its fit addon into unshacklarr/static, from the npm registry (npm checks each
# package's integrity): the page then loads nothing from a CDN. Bump the ?v= of their imports in index.html.
set -e
cd "$(dirname "$0")"
XTERM=5.5.0
FIT=0.10.0
tmp=$(mktemp -d)
(cd "$tmp" && npm pack --silent "@xterm/xterm@$XTERM" "@xterm/addon-fit@$FIT" >/dev/null && for f in *.tgz; do tar xzf "$f" && mv package "${f%.tgz}"; done)
out=../../unshacklarr/static
{ echo "/* xterm.js $XTERM, MIT License, Copyright (c) The xterm.js authors: https://github.com/xtermjs/xterm.js */"; cat "$tmp"/xterm-xterm-$XTERM/lib/xterm.js; } > $out/xterm.js
{ echo "/* @xterm/addon-fit $FIT, MIT License, Copyright (c) The xterm.js authors: https://github.com/xtermjs/xterm.js */"; cat "$tmp"/xterm-addon-fit-$FIT/lib/addon-fit.js; } > $out/xterm-fit.js
{ echo "/* xterm.js $XTERM, MIT License, Copyright (c) The xterm.js authors: https://github.com/xtermjs/xterm.js */"; cat "$tmp"/xterm-xterm-$XTERM/css/xterm.css; } > $out/xterm.css
sed -i.bak "/sourceMappingURL/d" $out/xterm.js $out/xterm-fit.js $out/xterm.css && rm -f $out/xterm*.bak  # no source maps shipped
rm -rf "$tmp"
