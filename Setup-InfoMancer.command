#!/bin/sh
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
"$SCRIPT_DIR/setup-infomancer.sh"
STATUS=$?
printf '\nPress Return to close this window...'
IFS= read -r _answer
exit "$STATUS"
