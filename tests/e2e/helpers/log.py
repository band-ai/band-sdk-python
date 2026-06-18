"""Pretty logging for E2E tests — a followable transcript under ``pytest -s``.

Use ``log_banner`` for section headers and ``log_step`` for in-scenario markers.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# Rich renders a readable transcript under ``pytest -s`` and degrades to plain
# text when stdout is captured/non-tty. All dynamic text is passed through
# ``rich.text.Text`` (no markup parsing) so values like "[Milk $3.50]" can't
# be misread as style tags.
_console = Console()

# Style + icon per step kind. Numeric/other steps fall back to the default.
_STEP_KINDS: dict[str, tuple[str, str]] = {
    "assert": ("bold green", "✔"),
    "restart": ("bold yellow", "⟳"),
    "retry": ("bold dark_orange", "↻"),
}
_STEP_DEFAULT: tuple[str, str] = ("bold cyan", "▶")


def log_banner(title: str) -> None:
    """Render a boxed section banner; green when it announces a pass."""
    passed = "PASS" in title.upper()
    _console.print()
    _console.print(
        Panel(
            Text(title, style="bold green" if passed else "bold bright_white"),
            border_style="green" if passed else "bright_cyan",
            padding=(0, 2),
            expand=True,
        )
    )


def log_step(n: int | str | float, text: str) -> None:
    """Render a color/icon-coded step marker within a scenario."""
    style, icon = _STEP_KINDS.get(str(n), _STEP_DEFAULT)
    label = str(n) if str(n) in _STEP_KINDS else f"step {n}"
    line = Text("  ")
    line.append(f"{icon} {label}", style=style)
    line.append("  ")
    line.append(text, style="white")
    _console.print(line)
