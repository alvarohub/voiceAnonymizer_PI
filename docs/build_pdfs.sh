#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/docs/pdf"
TMP="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

mkdir -p "$OUT"

CSS="$TMP/pdf.css"
cat > "$CSS" <<'CSS'
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 12px;
  line-height: 1.45;
  color: #1f2328;
  max-width: 920px;
  margin: 0 auto;
  padding: 24px 34px;
}
h1, h2, h3, h4 {
  color: #111827;
  page-break-after: avoid;
}
h1 {
  font-size: 26px;
  border-bottom: 1px solid #d0d7de;
  padding-bottom: 0.3em;
}
h2 {
  font-size: 20px;
  border-bottom: 1px solid #d8dee4;
  padding-bottom: 0.2em;
  margin-top: 1.7em;
}
h3 {
  font-size: 16px;
  margin-top: 1.4em;
}
pre, code {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 10.5px;
}
pre {
  background: #f6f8fa;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  padding: 10px 12px;
  overflow-wrap: break-word;
  white-space: pre-wrap;
}
code {
  background: #f6f8fa;
  border-radius: 4px;
  padding: 0.1em 0.25em;
}
pre code {
  background: transparent;
  padding: 0;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
  font-size: 11px;
}
th, td {
  border: 1px solid #d0d7de;
  padding: 6px 8px;
  vertical-align: top;
}
th {
  background: #f6f8fa;
  font-weight: 600;
}
p:has(img) {
  page-break-inside: avoid;
  margin: 1.1em 0 1.7em;
}
img {
  display: block;
  max-width: 100%;
  max-height: 7.8in;
  width: auto;
  height: auto;
  margin: 0 auto;
  border: 1px solid #d0d7de;
  border-radius: 6px;
}
a {
  color: #0969da;
  text-decoration: none;
}
@page {
  size: letter;
  margin: 0.55in;
}
CSS

CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
if [[ ! -x "$CHROME" ]]; then
  echo "Google Chrome not found at: $CHROME" >&2
  echo "Set CHROME=/path/to/chrome and rerun this script." >&2
  exit 1
fi

cd "$ROOT"

pandoc README.md \
  -f gfm \
  -t html5 \
  -s \
  --embed-resources \
  --resource-path=.:docs \
  --metadata pagetitle="SPEECH_RECORD_ANALYSIS README" \
  --css "$CSS" \
  -o "$TMP/README.html"

pandoc docs/central_collection.md \
  -f gfm \
  -t html5 \
  -s \
  --embed-resources \
  --resource-path=.:docs \
  --metadata pagetitle="Central Collection, CSV Logs, And Data Meaning" \
  --css "$CSS" \
  -o "$TMP/central_collection.html"

README_URL="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().as_uri())' "$TMP/README.html")"
CENTRAL_URL="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().as_uri())' "$TMP/central_collection.html")"

"$CHROME" \
  --headless \
  --disable-gpu \
  --no-sandbox \
  --allow-file-access-from-files \
  --print-to-pdf="$OUT/README.pdf" \
  "$README_URL"

"$CHROME" \
  --headless \
  --disable-gpu \
  --no-sandbox \
  --allow-file-access-from-files \
  --print-to-pdf="$OUT/central_collection.pdf" \
  "$CENTRAL_URL"

file "$OUT"/*.pdf