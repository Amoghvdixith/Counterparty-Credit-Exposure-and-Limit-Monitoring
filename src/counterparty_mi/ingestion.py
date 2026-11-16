"""Read raw source CSVs as text, without any type coercion or row dropping."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .schemas import SCHEMAS, SOURCE_FILES


@dataclass
class RawSources:
    tables: dict[str, pd.DataFrame | None]
    checksums: dict[str, str | None]
    files: dict[str, str]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_raw_table(path: Path) -> pd.DataFrame:
    """Read every value as a string; blanks stay as empty strings.

    ``source_row`` is the 1-based line number in the file (header = line 1).
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False, encoding="utf-8")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.astype(str)
    df.insert(0, "source_row", range(2, len(df) + 2))
    return df


def read_sources(raw_dir: str | Path) -> RawSources:
    raw_dir = Path(raw_dir)
    tables: dict[str, pd.DataFrame | None] = {}
    checksums: dict[str, str | None] = {}
    files: dict[str, str] = {}
    for name in SCHEMAS:
        path = raw_dir / SOURCE_FILES[name]
        files[name] = SOURCE_FILES[name]
        if path.is_file():
            tables[name] = read_raw_table(path)
            checksums[name] = sha256_file(path)
        else:
            tables[name] = None
            checksums[name] = None
    return RawSources(tables=tables, checksums=checksums, files=files)
