#!/usr/bin/env bash
# Package the plasmoid for distribution / KDE Store upload.
# A .plasmoid file is a zip of the plasmoid package directory.
set -euo pipefail
cd "$(dirname "$0")"

version=$(python3 -c "import json; print(json.load(open('plasmoid/org.nam20485.offpeaktray/metadata.json'))['KPlugin']['Version'])")
out="dist/org.nam20485.offpeaktray-${version}.plasmoid"
mkdir -p dist
rm -f "${out}" dist/org.nam20485.offpeaktray-*.plasmoid
(cd plasmoid && zip -qr "../${out}" org.nam20485.offpeaktray)
echo "created ${out}"
