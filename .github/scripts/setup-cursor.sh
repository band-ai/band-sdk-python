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

case "$(uname -m)" in
  x86_64 | amd64) arch=x64 ;;
  arm64 | aarch64) arch=arm64 ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

rm -rf "$install_dir"
mkdir -p "$install_dir"
case "$(uname -s)" in
  Linux* | Darwin*)
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    curl -fsSL "$package_url/$os/$arch/agent-cli-package.tar.gz" \
      | tar --strip-components=1 -xzf - -C "$install_dir"
    ln -s "$install_dir/cursor-agent" "$install_dir/agent"
    agent_dir="$install_dir"
    ;;
  MINGW* | MSYS*)
    archive="$install_dir/agent-cli-package.zip"
    curl -fsSL "$package_url/windows/$arch/agent-cli-package.zip" -o "$archive"
    powershell.exe -NoProfile -Command \
      "Expand-Archive -LiteralPath '$(cygpath -w "$archive")' -DestinationPath '$(cygpath -w "$install_dir")'"
    agent_dir="$install_dir/dist-package"
    # The launcher runs the bundled node.exe beside it; `agent` is the CLI's name.
    cp "$agent_dir/cursor-agent.cmd" "$agent_dir/agent.cmd"
    agent_dir="$(cygpath -w "$agent_dir")"
    if [[ -n "${GITHUB_ENV:-}" ]]; then
      printf 'CURSOR_COMMAND=%s\\agent.cmd acp\n' "$agent_dir" >> "$GITHUB_ENV"
    fi
    ;;
  *) echo "Unsupported operating system: $(uname -s)" >&2; exit 1 ;;
esac

if [[ -n "${GITHUB_PATH:-}" ]]; then
  printf '%s\n' "$agent_dir" >> "$GITHUB_PATH"
fi
echo "Installed Cursor CLI ${CURSOR_CLI_VERSION} in ${agent_dir}"
