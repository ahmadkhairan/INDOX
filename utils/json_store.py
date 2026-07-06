from __future__ import annotations

"""
utils/json_store.py — Atomic JSON read/write with versioned backups.

Changes from original:
  - write_json() creates a rotating backup (.bak.1 … .bak.N) before overwriting.
  - MAX_BACKUPS controls how many backups are kept (default 5).
  - read_json() validates structural type (dict / list) when `expected_type`
    is given, falling back to default on mismatch so semantic corruption is
    caught early.
  - Public helpers:
      write_json(path, data, ...)
      read_json(path, default, expected_type=None)
      restore_latest_backup(path) -> bool
"""

import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Type

import fcntl

MAX_BACKUPS: int = 5


@contextmanager
def file_lock(path: str) -> Iterator[None]:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _backup_path(path: str, n: int) -> str:
    return f"{path}.bak.{n}"


def _rotate_backups(path: str) -> None:
    """
    Shift existing backups: .bak.4 → dropped, .bak.3 → .bak.4, …, live → .bak.1
    """
    for i in range(MAX_BACKUPS - 1, 0, -1):
        src = _backup_path(path, i)
        dst = _backup_path(path, i + 1)
        if os.path.exists(src):
            try:
                os.replace(src, dst)
            except OSError:
                pass
    if os.path.exists(path):
        try:
            import shutil

            shutil.copy2(path, _backup_path(path, 1))
        except OSError:
            pass


def read_json(
    path: str,
    default: Any,
    expected_type: Type | None = None,
) -> Any:
    """
    Read JSON from *path* and return its contents.

    Parameters
    ----------
    path          : file path to read
    default       : value to return on any error
    expected_type : if given (e.g. dict, list), the parsed value is checked
                    against this type.  A mismatch is treated as corruption and
                    `default` is returned.
    """
    lock_path = f"{path}.lock"
    with file_lock(lock_path):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if expected_type is not None and not isinstance(data, expected_type):
                raise TypeError(
                    f"Expected {expected_type.__name__}, "
                    f"got {type(data).__name__} in {path}"
                )
            return data
        except Exception:
            return default


def write_json(
    path: str,
    data: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
    backup: bool = True,
) -> None:
    """
    Atomically write *data* as JSON to *path*.

    If *backup* is True (default), the existing file is rotated into a
    numbered backup slot before being overwritten.
    """
    lock_path = f"{path}.lock"
    with file_lock(lock_path):
        if backup:
            _rotate_backups(path)
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=".tmp-json-", suffix=".json", dir=directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=ensure_ascii, indent=indent)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


def restore_latest_backup(path: str) -> bool:
    """
    Replace *path* with its most recent backup (.bak.1).
    Returns True on success, False if no backup exists.
    """
    bak = _backup_path(path, 1)
    if not os.path.exists(bak):
        return False
    try:
        import shutil

        shutil.copy2(bak, path)
        return True
    except OSError:
        return False


def list_backups(path: str) -> list[str]:
    """Return existing backup paths in order (most recent first)."""
    result = []
    for i in range(1, MAX_BACKUPS + 1):
        bp = _backup_path(path, i)
        if os.path.exists(bp):
            result.append(bp)
    return result
