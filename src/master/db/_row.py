"""Row wrapper that makes SQLAlchemy rows behave like sqlite3.Row.

Supports both named column access (row["col"]) and positional access (row[0]),
plus dict-conversion via keys() so dict(row) produces {"col": val, ...}.
"""
from __future__ import annotations


class _Row:
    """Wraps a SQLAlchemy Row to be compatible with sqlite3.Row usage patterns."""

    __slots__ = ("_row", "_mapping", "_keys_list")

    def __init__(self, row) -> None:
        self._row = row
        self._mapping = row._mapping
        self._keys_list = list(row._mapping.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._row[key]
        return self._mapping[key]

    def keys(self):
        return self._keys_list

    def __iter__(self):
        return iter(self._mapping.values())

    def __repr__(self) -> str:
        return repr(dict(zip(self._keys_list, list(self._mapping.values()))))

    def get(self, key, default=None):
        return self._mapping.get(key, default)

    def __bool__(self) -> bool:
        return True
