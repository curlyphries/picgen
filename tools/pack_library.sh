#!/usr/bin/env bash
# Build the library release zip from a library folder.
#   tools/pack_library.sh [library dir] [out.zip] [kinds]
# Default kinds are the character-free ones: top,pants,shorts,underwear,socks,footwear,headwear,accessory.
# Items that record a source character (expressions, poses and activities drawn with someone) are never included
# unless you list their kind explicitly AND they have no source.
set -euo pipefail
LIB="${1:-$(cd "$(dirname "$0")/.." && pwd)/library}"
OUT="${2:-picgen-library-v1.zip}"
KINDS="${3:-top,pants,shorts,underwear,socks,footwear,headwear,accessory}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
python3 - "$LIB" "$TMP" "$KINDS" <<'PY'
import json, shutil, sys, pathlib
lib, tmp, kinds = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), set(sys.argv[3].split(","))
idx = json.load(open(lib / "index.json"))
keep = {t: m for t, m in idx.items() if m.get("kind") in kinds and not m.get("source")}
for t, m in keep.items():
    shutil.copy(lib / m.get("file", t + ".png"), tmp / m.get("file", t + ".png"))
json.dump(keep, open(tmp / "index.json", "w"), indent=1)
print(f"{len(keep)} items of {len(idx)} selected")
PY
( cd "$TMP" && zip -q -0 -r "$OLDPWD/$OUT" . )
ls -la "$OUT"
