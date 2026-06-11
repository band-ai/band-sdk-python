"""Unit tests for is_no_clean_mode helper function."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.conftest_integration import is_no_clean_mode


class TestIsNoCleanMode:
    """Tests for the is_no_clean_mode helper function."""

    def test_returns_false_by_default(self, monkeypatch):
        """Should return False when no env var or option is set."""
        monkeypatch.delenv("BAND_TEST_NO_CLEAN", raising=False)
        assert is_no_clean_mode() is False

    def test_returns_true_with_env_var_1(self, monkeypatch):
        """Should return True when BAND_TEST_NO_CLEAN=1."""
        monkeypatch.setenv("BAND_TEST_NO_CLEAN", "1")
        assert is_no_clean_mode() is True

    def test_returns_true_with_env_var_true(self, monkeypatch):
        """Should return True when BAND_TEST_NO_CLEAN=true."""
        monkeypatch.setenv("BAND_TEST_NO_CLEAN", "true")
        assert is_no_clean_mode() is True

    def test_returns_true_with_env_var_yes(self, monkeypatch):
        """Should return True when BAND_TEST_NO_CLEAN=yes."""
        monkeypatch.setenv("BAND_TEST_NO_CLEAN", "yes")
        assert is_no_clean_mode() is True

    def test_returns_true_with_env_var_TRUE_uppercase(self, monkeypatch):
        """Should return True when BAND_TEST_NO_CLEAN=TRUE (case insensitive)."""
        monkeypatch.setenv("BAND_TEST_NO_CLEAN", "TRUE")
        assert is_no_clean_mode() is True

    def test_returns_false_with_invalid_env_var(self, monkeypatch):
        """Should return False with invalid env var value."""
        monkeypatch.setenv("BAND_TEST_NO_CLEAN", "false")
        assert is_no_clean_mode() is False

    def test_returns_false_with_empty_env_var(self, monkeypatch):
        """Should return False when BAND_TEST_NO_CLEAN is empty string."""
        monkeypatch.setenv("BAND_TEST_NO_CLEAN", "")
        assert is_no_clean_mode() is False

    def test_returns_true_with_pytest_option(self, monkeypatch):
        """Should return True when --no-clean pytest option is set."""
        monkeypatch.delenv("BAND_TEST_NO_CLEAN", raising=False)

        mock_config = MagicMock()
        mock_config.getoption.return_value = True

        mock_request = MagicMock()
        mock_request.config = mock_config

        assert is_no_clean_mode(mock_request) is True
        mock_config.getoption.assert_called_once_with("--no-clean", default=False)

    def test_returns_false_with_pytest_option_false(self, monkeypatch):
        """Should return False when --no-clean pytest option is not set."""
        monkeypatch.delenv("BAND_TEST_NO_CLEAN", raising=False)

        mock_config = MagicMock()
        mock_config.getoption.return_value = False

        mock_request = MagicMock()
        mock_request.config = mock_config

        assert is_no_clean_mode(mock_request) is False

    def test_env_var_takes_precedence_over_pytest_option(self, monkeypatch):
        """Env var should take precedence - True from env even if option is False."""
        monkeypatch.setenv("BAND_TEST_NO_CLEAN", "1")

        mock_config = MagicMock()
        mock_config.getoption.return_value = False

        mock_request = MagicMock()
        mock_request.config = mock_config

        # Env var is checked first and returns True
        assert is_no_clean_mode(mock_request) is True
        # getoption should not even be called since env var returned True
        mock_config.getoption.assert_not_called()

    def test_handles_getoption_value_error(self, monkeypatch):
        """Should handle ValueError when --no-clean option not registered."""
        monkeypatch.delenv("BAND_TEST_NO_CLEAN", raising=False)

        mock_config = MagicMock()
        mock_config.getoption.side_effect = ValueError("Option not registered")

        mock_request = MagicMock()
        mock_request.config = mock_config

        # Should return False when option is not registered
        assert is_no_clean_mode(mock_request) is False
