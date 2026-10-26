"""Helpers for reading the data CSV files."""
from __future__ import annotations

from typing import Iterable, Iterator


def no_nul(lines: Iterable[str]) -> Iterator[str]:
    """
    Drops NUL bytes - a power cut while writing leaves them in a file
    (the csv module raises "line contains NULL byte" on them).
    """
    for line in lines:
        yield line.replace("\x00", "") if "\x00" in line else line
