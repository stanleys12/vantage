#!/bin/zsh
# launchd gives a minimal PATH; pin the project venv interpreter.
cd /Users/stanleyshen/vantage || exit 1
PY=/Users/stanleyshen/vantage/.venv/bin/python
if [ ! -x "$PY" ]; then echo "$(date) FATAL: venv python missing" >> reports/run.log; exit 1; fi
"$PY" run_daily.py "$@" >> reports/cron.log 2>&1
