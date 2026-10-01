#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${RESULTS_DIR:?RESULTS_DIR must be set}"
mkdir -p -- "$RESULTS_DIR"
RESULTS_DIR="$(cd -- "$RESULTS_DIR" && pwd)"
export RESULTS_DIR
python3 "$script_dir/report.py" "$RESULTS_DIR"
work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
uv venv --python 3.12 "$work_dir/venv"
uv pip install --python "$work_dir/venv/bin/python" --only-binary :all: "band-sdk==${BAND_SDK_VERSION:-3.2.1}"
cd -- "$work_dir"
unset PYTHONPATH PYTHONHOME
"$work_dir/venv/bin/python" -I "$script_dir/smoke.py" "$script_dir/../.."
