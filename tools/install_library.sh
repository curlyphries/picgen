#!/usr/bin/env bash
# Download the shared reference library (558 items, about 380 MB) into LIBRARY_DIR (default ./library).
#
#   tools/install_library.sh            # fetch from the GitHub release and unpack
#   LIBRARY_DIR=/data/lib tools/install_library.sh
#
# The library is a GitHub release asset, not part of the git history, because it is 380 MB of PNGs.
# Contents: 62 expressions, 46 poses, 96 activities, 56 outfits, 24 tops, 14 pants, 7 shorts, 3 underwear,
# 12 socks, 78 footwear, 80 headwear, 80 accessories. Garments are front | side | back composites.
# Existing files are kept; only missing ones are unpacked, so it is safe to rerun on a library you have added to.
set -euo pipefail
REPO="${PICGEN_REPO:-curlyphries/picgen}"
TAG="${PICGEN_LIBRARY_TAG:-library-v1}"
ASSET="picgen-library-v1.zip"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LIB="${LIBRARY_DIR:-$HERE/library}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

mkdir -p "$LIB"
echo "library: $LIB"
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --dir "$TMP"     # works for private repos too
else
  curl -L --fail --retry 5 -o "$TMP/$ASSET" "https://github.com/$REPO/releases/download/$TAG/$ASSET"
fi

have_index=0; [ -f "$LIB/index.json" ] && have_index=1
cd "$TMP" && unzip -q -n "$ASSET" -d unpacked
n_new=0
for f in unpacked/*.png; do
  b="$(basename "$f")"
  if [ ! -f "$LIB/$b" ]; then cp "$f" "$LIB/$b"; n_new=$((n_new+1)); fi
done
if [ "$have_index" = 1 ]; then
  # merge: keep your own entries, add the shipped ones you do not have
  python3 - "$LIB/index.json" unpacked/index.json <<'PY'
import json,sys
mine=json.load(open(sys.argv[1])); shipped=json.load(open(sys.argv[2]))
added=0
for k,v in shipped.items():
    if k not in mine: mine[k]=v; added+=1
json.dump(mine, open(sys.argv[1],"w"), indent=1); print(f"index: merged, {added} entries added")
PY
else
  cp unpacked/index.json "$LIB/index.json"; echo "index: installed"
fi
echo "pictures added: $n_new"
echo "Done. A running picgen picks the library up on the next request (no restart)."
