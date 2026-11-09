#!/bin/sh
# The clean-environment test gate: every tests/test_*.py module in its own
# process and scratch home, in parallel. Exits 1 when any module fails.
# `agentfleet ci` runs it through .agentfleet-ci.
set -u
cd "$(dirname "$0")/.." || exit 2
find . -name __pycache__ -not -path './.git/*' -prune -exec rm -rf {} +
homes=$(mktemp -d)
logs=$(mktemp -d)
ls tests/test_*.py | sed 's#tests/##;s#\.py$##' | HOMES="$homes" LOGS="$logs" xargs -P 6 -I{} sh -c '
  H=$(mktemp -d -p "$HOMES")
  env -i PATH="$PATH" HOME="$H" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
    python3 -B -m unittest tests.{} >"$LOGS/{}.log" 2>&1 || echo "FAIL {}" >>"$LOGS/failed"'
total=$(cat "$logs"/*.log | grep -E "^Ran [0-9]+" | awk '{s+=$2} END {print s+0}')
status=0
if [ -s "$logs/failed" ]; then
  cat "$logs/failed"
  for name in $(sed 's/^FAIL //' "$logs/failed"); do tail -n 30 "$logs/$name.log"; done
  status=1
fi
echo "test-gate: $total tests, $( [ $status -eq 0 ] && echo all modules passed || echo some modules failed)"
rm -rf "$homes" "$logs"
exit $status
