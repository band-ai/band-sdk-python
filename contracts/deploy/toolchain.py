"""Resolve and validate the native toolchain before leaving the source checkout."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TypedDict

MINIMUM_OTP = (27, 3, 4, 17)


class Toolchain(TypedDict):
    elixir: str
    erlang: str
    bin_paths: list[str]


def select(source: Path) -> Toolchain:
    probe = """
    IO.puts(Path.expand("../..", Application.app_dir(:elixir)))
    root = :code.root_dir() |> to_string()
    IO.puts(root)
    IO.puts(System.version())
    otp = :erlang.system_info(:otp_release) |> to_string()
    IO.puts(File.read!(Path.join([root, "releases", otp, "OTP_VERSION"])) |> String.trim())
    """
    lines = subprocess.check_output(
        ["elixir", "--erl", "+S 2:2", "-e", probe], cwd=source, text=True
    ).splitlines()
    if len(lines) != 4:
        raise ValueError("Cannot identify the selected Elixir/OTP toolchain")
    elixir_root, erlang_root, elixir, erlang = lines
    if not re.fullmatch(r"\d+(?:\.\d+)*", erlang):
        raise ValueError("Unsupported OTP version: " + erlang)
    otp = tuple(map(int, erlang.split(".")))
    if otp[0] != 27 or otp < MINIMUM_OTP:
        raise ValueError(
            "Release builds require OTP >=27.3.4.17,<28; selected " + erlang
        )
    if not re.fullmatch(r"\d+\.\d+\.\d+", elixir) or not (1, 18, 0) <= tuple(
        map(int, elixir.split("."))
    ) < (2, 0, 0):
        raise ValueError(
            "Release builds require stable Elixir >=1.18,<2; selected " + elixir
        )
    return {
        "elixir": elixir,
        "erlang": erlang,
        "bin_paths": [str(Path(root) / "bin") for root in (elixir_root, erlang_root)],
    }
