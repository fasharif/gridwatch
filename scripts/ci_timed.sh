#!/usr/bin/env bash
# Run a command and append "label<TAB>seconds<TAB>exit code" to $TIMINGS_FILE, so the daily
# workflow can show how long each step took on the hosted runner (see pipeline.yml).
#
# Usage: bash scripts/ci_timed.sh LABEL COMMAND [ARGUMENTS...]
set -uo pipefail

if [ "$#" -lt 2 ]; then
    echo "usage: $0 LABEL COMMAND [ARGUMENTS...]" >&2
    exit 64
fi

label="$1"
shift
timings="${TIMINGS_FILE:-${RUNNER_TEMP:-/tmp}/gridwatch-timings.tsv}"
start="$(date +%s)"
"$@"
status=$?
printf '%s\t%s\t%s\n' "$label" "$(($(date +%s) - start))" "$status" >> "$timings"
exit "$status"
