#!/usr/bin/env bash
# Keep all qualification descendants in one runner-owned service cgroup.
set -euo pipefail

mode=${1:?mode is required}
source_root=${2:?source root is required}
output=${3:?output directory is required}
unit=${4:?unit name is required}
if [[ ! "$unit" =~ ^band-contract-[0-9]+-[0-9]+\.service$ ]]; then
  echo "Invalid contract qualification unit: $unit" >&2
  exit 2
fi
cgroup="/sys/fs/cgroup/system.slice/$unit"

stop_owned_unit() {
  mkdir -p "$output" || return 1
  local load_state
  load_state=$(sudo systemctl show "$unit" --property=LoadState --value) || return 1
  if [[ "$load_state" != not-found ]]; then
    sudo systemctl stop "$unit" || return 1
  fi
  sudo systemctl show "$unit" \
    --property=LoadState,ActiveState,SubState,MainPID,ControlGroup,Result \
    | tee "$output/cleanup.log" || return 1
  if ! grep -qx 'MainPID=0' "$output/cleanup.log" \
    || ! grep -Eq '^ActiveState=(inactive|failed)$' "$output/cleanup.log"; then
    echo "Contract qualification unit still has active processes" >&2
    return 1
  fi
  if [[ -d "$cgroup" ]]; then
    if ! grep -qx 'populated 0' "$cgroup/cgroup.events"; then
      echo "Contract qualification cgroup is still populated: $cgroup" >&2
      return 1
    fi
    printf 'cgroup=%s\npopulated=0\n' "$cgroup" >> "$output/cleanup.log" || return 1
  else
    printf 'cgroup=%s\nabsent=true\n' "$cgroup" >> "$output/cleanup.log" || return 1
  fi
}

cleanup_on_exit() {
  local status=$?
  trap - EXIT INT TERM
  if ! stop_owned_unit; then
    status=1
  fi
  exit "$status"
}

case "$mode" in
  run)
    mkdir "$output"
    trap cleanup_on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    # The service runs as the Actions runner, including dependency installation.
    # Its cgroup also owns callbacks that start a new session or process group.
    sudo systemd-run \
      --unit="$unit" --slice=system.slice \
      --uid="$(id -u)" --gid="$(id -g)" \
      --working-directory="$source_root" \
      --property=Type=exec --property=KillMode=control-group \
      --property=TimeoutStartSec=30s \
      --property=RuntimeMaxSec=50min --property=TimeoutStopSec=30s \
      --property=SendSIGKILL=yes --property=UMask=0077 \
      --setenv="PATH=$PATH" --setenv="HOME=$HOME" \
      --setenv=LANG=C.UTF-8 --setenv=TZ=UTC --setenv=CI=true \
      --setenv="UV_PROJECT_ENVIRONMENT=$output/bootstrap" \
      --setenv=UV_PYTHON_DOWNLOADS=never \
      --expand-environment=no --collect --wait --pipe \
      /bin/bash "$source_root/.github/scripts/contractqualification.sh" \
      execute "$source_root" "$output" "$unit" 2>&1 | tee "$output/driver.log"
    ;;
  execute)
    cd "$source_root"
    uv sync --all-packages --locked --extra dev --extra contracts \
      --python /usr/bin/python3.12
    exec "$output/bootstrap/bin/python" -m contracts.deploy.ci \
      "$output/qualification" --source-root "$source_root"
    ;;
  stop)
    stop_owned_unit
    ;;
  *)
    echo "Unknown contract qualification mode: $mode" >&2
    exit 2
    ;;
esac
