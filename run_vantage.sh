#!/bin/zsh
# launchd gives a minimal PATH; pin the project venv interpreter.
cd "${0:A:h}" || exit 1
mkdir -p reports
PY=./.venv/bin/python
if [ ! -x "$PY" ]; then echo "$(date) FATAL: venv python missing" >> reports/run.log; exit 1; fi
"$PY" run_daily.py "$@" >> reports/cron.log 2>&1
