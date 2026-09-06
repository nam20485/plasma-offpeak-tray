#!/usr/bin/env bash
# Validation gate: build → scan → test, fail-fast.
#
# Choices (no CI workflow exists yet; these are the documented defaults):
#   build: python3 -m py_compile on the tooling + JSON syntax checks of the
#          plasmoid metadata and example config.
#   scan:  gitleaks when installed (optional dev dependency), else a warning.
#   test:  stdlib unittest suite (python3 only — no third-party deps).
set -euo pipefail
cd "$(dirname "$0")"

echo "== build =="
python3 -m py_compile scripts/offpeak.py \
    skills/find-offpeak-windows/scripts/update_schedule.py \
    tests/test_offpeak.py tests/test_update_schedule.py
python3 -m json.tool config/schedules.example.json > /dev/null
python3 -m json.tool plasmoid/org.nam20485.offpeaktray/metadata.json > /dev/null
echo "build ok"

echo "== scan =="
if command -v gitleaks > /dev/null 2>&1; then
    gitleaks detect --no-git --source .
else
    echo "scan: gitleaks not found — skipped (install gitleaks for secret scanning)"
fi
echo "scan ok"

echo "== test =="
python3 -m unittest discover -s tests -v
echo "test ok"
