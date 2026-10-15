"""Process entry for the Windows Codex job helper.

``python -m band.integrations.codex.winmain`` is the command the parent
starts. The package imports :mod:`band.integrations.codex.winspawn` while
loading, so running that module itself makes Python warn that it is already
in ``sys.modules`` and abort when warnings are errors.
"""

from __future__ import annotations

from band.integrations.codex.winspawn import main

if __name__ == "__main__":
    raise SystemExit(main())
