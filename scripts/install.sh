#!/usr/bin/env sh
set -eu

python_bin="${PYTHON:-python3}"
extra=""
if [ "${1:-}" = "--release" ]; then
  shift
  exec "$(dirname "$0")/install-release.sh" "$@"
elif [ "${1:-}" = "--dev" ]; then
  extra="[dev]"
elif [ -n "${1:-}" ]; then
  printf '%s\n' "usage: $0 [--dev] | --release [release installer options]" >&2
  exit 2
fi

"$python_bin" -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else "JSON API Forge requires Python 3.10-3.14")'
"$python_bin" -m venv .venv
if [ -x .venv/bin/python ]; then
  venv_python=.venv/bin/python
else
  venv_python=.venv/Scripts/python.exe
fi
"$venv_python" -m pip install --upgrade pip
"$venv_python" -m pip install -e ".$extra"
"$venv_python" -m pip check
printf '%s\n' "Installed JSON API Forge into .venv"
printf '%s\n' "Activate it, then run: forge new MyService --slug my-service"
