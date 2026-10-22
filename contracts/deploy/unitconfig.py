"""Read service-unit directives with their case-sensitive names intact."""

from __future__ import annotations

from configparser import ConfigParser


class UnitParser(ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr
