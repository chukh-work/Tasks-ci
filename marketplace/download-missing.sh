#!/usr/bin/env bash
set -euo pipefail

MOODLE_KEY="${1:?usage: download-missing.sh <manifest-key, e.g. moodle_4.2>}"
BASE_URL="${MOODLE_MARKETPLACE_BASE:-https://marketplace.moodle.com}"
MANIFEST="marketplace/manifest.json"
COOKIE="${MOODLE_MARKETPLACE_COOKIE:-}"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
STAGE="staging_${MOODLE_KEY}"
FAIL=0

CURL_OPTS=(-fsSL --retry 3 --max-time 180 -A "$UA")
if [ -n "$COOKIE" ]; then
  CURL_OPTS+=(-H "Cookie: $COOKIE")
fi

fetch() { curl "${CURL_OPTS[@]}" "$@"; }

echo "### Manifest key: $MOODLE_KEY"
mkdir -p "$STAGE"

# entries.tsv: component<TAB>dest<TAB>plugin_id<TAB>version_id<TAB>want_version
python3 - "$MANIFEST" "$MOODLE_KEY" > entries.tsv <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
for e in data[sys.argv[2]]:
    print("\t".join([
        e["component"],
        e["dest"],
        str(e.get("plugin_id") or ""),
        str(e.get("version_id") or ""),
        e.get("want_version", ""),
    ]))
PY

while IFS=$'\t' read -r component dest plugin_id version_id want; do
  echo
  echo "=== $component -> $dest (want: ${want:-any})"
  if [ -z "$plugin_id" ]; then
    echo "-- discovering plugin id via search page..."
    search_html=$(fetch "$BASE_URL/plugins?search=${component}" || true)
    plugin_id=$(printf '%s' "$search_html" | grep -oE '/plugins/[0-9]+' | grep -oE '[0-9]+' | head -n1 || true)
    if [ -z "$plugin_id" ]; then
      echo "!! FAILED: cannot discover plugin_id for $component."
      echo "   Open $BASE_URL in a browser, find the plugin, copy the numeric id from the URL and put it into marketplace/manifest.json"
      FAIL=1; continue
    fi
    echo "-- discovered plugin_id=$plugin_id (MANUALLY VERIFY!)"
  fi

  if [ -z "$version_id" ]; then
    echo "-- discovering version id from versions page..."
    versions_html=$(fetch "$BASE_URL/plugins/$plugin_id/versions" || true)
    version_id=$(printf '%s' "$versions_html" | grep -oE "/plugins/$plugin_id/versions/[0-9]+" | grep -oE '[0-9]+' | head -n1 || true)
    if [ -z "$version_id" ]; then
      echo "!! FAILED: cannot discover version_id for $component (plugin $plugin_id)."
      echo "   Open $BASE_URL/plugins/$plugin_id/versions, pick the version for $MOODLE_KEY, copy its numeric id into marketplace/manifest.json"
      FAIL=1; continue
    fi
    echo "-- picked version_id=$version_id (NEWEST listed; MANUALLY VERIFY it supports $MOODLE_KEY!)"
  fi

  zip_out="$STAGE/$component.zip"
  if ! fetch -o "$zip_out" "$BASE_URL/plugins/$plugin_id/versions/$version_id/download"; then
    echo "!! FAILED to download $component: $BASE_URL/plugins/$plugin_id/versions/$version_id/download"
    echo "   If the marketplace requires login, set repo secret MOODLE_MARKETPLACE_COOKIE"
    FAIL=1; continue
  fi

  magic=$(head -c 2 "$zip_out" || true)
  if [ "$magic" != "PK" ]; then
    echo "!! Download is not a zip (probably a login/error page). First bytes:"
    head -c 400 "$zip_out" || true
    echo
    rm -f "$zip_out"
    FAIL=1; continue
  fi

  tmp="$STAGE/.x_$component"
  rm -rf "$tmp"; mkdir -p "$tmp"
  unzip -q -o "$zip_out" -d "$tmp"
  rm -f "$zip_out"

  top=$(ls -A "$tmp")
  if [ "$(printf '%s' "$top" | wc -l)" -eq 1 ] && [ -d "$tmp/$top" ]; then
    src="$tmp/$top"
  else
    src="$tmp"
  fi

  mkdir -p "$(dirname "$STAGE/$dest")"
  rm -rf "$STAGE/$dest"
  cp -R "$src" "$STAGE/$dest"
  rm -rf "$tmp"
  echo "-- OK: $component -> $STAGE/$dest"
done < entries.tsv

if [ "$FAIL" -ne 0 ]; then
  echo
  echo "!!! Some plugins failed. See messages above."
fi

mkdir -p zips
rm -f "zips/missing_${MOODLE_KEY}.zip"
( cd "$STAGE" && zip -qr "../zips/missing_${MOODLE_KEY}.zip" . )
echo
echo "### Done. Artifact: zips/missing_${MOODLE_KEY}.zip"
[ "$FAIL" -eq 0 ]
