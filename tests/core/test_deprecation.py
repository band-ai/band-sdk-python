"""Tests for BandDeprecationWarning / warn_deprecated."""

from __future__ import annotations

import warnings


from band.core.deprecation import BandDeprecationWarning, warn_deprecated


def test_warn_deprecated_emits_band_deprecation_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warn_deprecated("old_name", "new_name", "2.0", stacklevel=1)

    assert len(caught) == 1
    assert issubclass(caught[0].category, BandDeprecationWarning)
    assert issubclass(caught[0].category, FutureWarning)
    assert "old_name" in str(caught[0].message)
    assert "new_name" in str(caught[0].message)
    assert "2.0" in str(caught[0].message)


def test_warn_deprecated_message_override() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warn_deprecated(
            "old",
            "new",
            message="custom deprecation text",
            stacklevel=1,
        )

    assert str(caught[0].message) == "custom deprecation text"


def test_warn_deprecated_message_only() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warn_deprecated(message="custom deprecation text", stacklevel=1)
    assert len(caught) == 1
    assert "custom deprecation text" in str(caught[0].message)
