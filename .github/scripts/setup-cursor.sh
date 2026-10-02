#!/usr/bin/env bash
# Install a pinned Cursor agent CLI for the `backends` e2e lane's cursor_acp adapter.
#
# Auth is the CURSOR_API_KEY the baseline step passes to this lane only, so no
# login step is involved.
set -euo pipefail

# Pinned like Copilot and OMP: Cursor's installer always serves its newest build,
# so an ACP mode or plan-message change would land as an unrelated-looking lane
# failure. Bump deliberately; the package URL is the one Cursor's installer reads.
CURSOR_CLI_VERSION="${CURSOR_CLI_VERSION:-2026.09.28-64d2043}"
package_url="https://downloads.cursor.com/lab/${CURSOR_CLI_VERSION}"
install_dir="$HOME/.cursor-agent-${CURSOR_CLI_VERSION}"
# Only this dir joins PATH: the package root also ships its own node and rg,
# which would shadow the runner's for every later step (npm -g then installs
# under the wrong prefix).
bin_dir="$install_dir/bin"

native_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

case "$(uname -m)" in
  x86_64 | amd64) arch=x64 ;;
  arm64 | aarch64) arch=arm64 ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

# A cache restore leaves a complete install; only a miss downloads.
case "$(uname -s)" in
  Linux* | Darwin*)
    if [[ ! -x "$bin_dir/agent" ]]; then
      rm -rf "$install_dir"
      mkdir -p "$bin_dir"
      os="$(uname -s | tr '[:upper:]' '[:lower:]')"
      curl -fsSL "$package_url/$os/$arch/agent-cli-package.tar.gz" \
        | tar --strip-components=1 -xzf - -C "$install_dir"
      # The launcher finds its bundled node through realpath.
      ln -s "$install_dir/cursor-agent" "$bin_dir/agent"
    fi
    ;;
  MINGW* | MSYS*)
    package_dir="$install_dir/dist-package"
    if [[ ! -f "$bin_dir/agent.cmd" ]]; then
      rm -rf "$install_dir"
      mkdir -p "$bin_dir"
      archive="$install_dir/agent-cli-package.zip"
      curl -fsSL "$package_url/windows/$arch/agent-cli-package.zip" -o "$archive"
      powershell.exe -NoProfile -Command \
        "Expand-Archive -LiteralPath '$(cygpath -w "$archive")' -DestinationPath '$(cygpath -w "$install_dir")'"
      rm "$archive"
      # The launcher runs the bundled node.exe beside it (%~dp0), so it stays in
      # the package and `agent` is a shim that calls it.
      cp "$package_dir/cursor-agent.cmd" "$package_dir/agent.cmd"
      printf '@call "%s\\agent.cmd" %%*\r\n' "$(cygpath -w "$package_dir")" > "$bin_dir/agent.cmd"
    fi
    if [[ -n "${GITHUB_ENV:-}" ]]; then
      printf 'CURSOR_COMMAND=%s\\agent.cmd acp\n' "$(cygpath -w "$package_dir")" >> "$GITHUB_ENV"
    fi
    ;;
  *) echo "Unsupported operating system: $(uname -s)" >&2; exit 1 ;;
esac

if [[ -n "${GITHUB_PATH:-}" ]]; then
  printf '%s\n' "$(native_path "$bin_dir")" >> "$GITHUB_PATH"
fi
echo "Installed Cursor CLI ${CURSOR_CLI_VERSION} in ${install_dir}"
